import functools
import os
import uuid
from dataclasses import asdict

from flask import (Flask, render_template, request, redirect, url_for,
                    session, flash, send_file, jsonify, abort)
from PIL import Image

import db
import rules_engine as rules
import ocr_engine as ocr
from report_pdf import build_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB uploads

db.init_db()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user", {}).get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    return {"current_user": session.get("user")}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        user = db.verify_user(u, p)
        if user:
            session["user"] = user
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user" in session else url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    stats = db.dashboard_stats()
    return render_template("dashboard.html", stats=stats)


# ---------------------------------------------------------------------------
# Scan / upload
# ---------------------------------------------------------------------------

@app.route("/scan", methods=["GET", "POST"])
@login_required
def scan():
    if request.method == "GET":
        return render_template("scan.html")

    file = request.files.get("image")
    if not file or file.filename == "":
        flash("Please choose an image to scan.", "error")
        return redirect(url_for("scan"))

    ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    file.save(fpath)

    try:
        image = Image.open(fpath)
        image.load()
    except Exception:
        flash("That file doesn't look like a valid image.", "error")
        return redirect(url_for("scan"))

    pdp_width_cm = _to_float(request.form.get("pdp_width_cm"))
    pdp_height_cm = _to_float(request.form.get("pdp_height_cm"))

    text = ocr.extract_text(image)
    declarations = rules.check_declarations(text)

    words, (img_w_px, _) = ocr.extract_words(image)
    if pdp_width_cm:
        heights_mm = ocr.net_quantity_glyph_heights_mm(words, img_w_px, pdp_width_cm)
        pdp_area = (pdp_width_cm * (pdp_height_cm or pdp_width_cm))
        font_result = rules.check_font_size(heights_mm, pdp_area)
    else:
        font_result = rules.DeclarationResult(
            code="font_size", label="Net quantity font size", rule_ref="Rule 6 / Second Schedule",
            status=rules.Status.REVIEW,
            detail="Principal Display Panel dimensions were not provided — measure manually or "
                   "re-scan with panel width/height filled in.",
        )
    declarations.append(font_result)

    report = rules.build_report(declarations)

    decl_dicts = [
        {"code": d.code, "label": d.label, "rule_ref": d.rule_ref,
         "status": d.status.value, "detail": d.detail, "matched_text": d.matched_text}
        for d in declarations
    ]

    scan_id = db.save_scan(
        product_name=request.form.get("product_name") or None,
        brand=request.form.get("brand") or None,
        category=request.form.get("category") or None,
        image_path=f"uploads/{fname}",
        pdp_width_cm=pdp_width_cm,
        pdp_height_cm=pdp_height_cm,
        ocr_text=text,
        overall_status=report.overall_status.value,
        score=report.score,
        declarations=decl_dicts,
        scanned_by=session["user"]["username"],
        location=request.form.get("location") or None,
        notes=request.form.get("notes") or None,
    )

    return redirect(url_for("view_report", scan_id=scan_id))


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Report view + PDF export
# ---------------------------------------------------------------------------

@app.route("/report/<int:scan_id>")
@login_required
def view_report(scan_id):
    scan_row = db.get_scan(scan_id)
    if not scan_row:
        abort(404)
    import json
    declarations = json.loads(scan_row["declarations_json"])
    return render_template("report.html", scan=scan_row, declarations=declarations)


@app.route("/report/<int:scan_id>/pdf")
@login_required
def report_pdf(scan_id):
    scan_row = db.get_scan(scan_id)
    if not scan_row:
        abort(404)
    import json
    declarations = json.loads(scan_row["declarations_json"])
    image_path = os.path.join(BASE_DIR, "static", scan_row["image_path"])
    pdf_bytes = build_pdf(scan_row, declarations, image_path=image_path if os.path.exists(image_path) else None)
    return send_file(
        __import__("io").BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"compliance_report_{scan_id}.pdf",
    )


@app.route("/report/<int:scan_id>/json")
@login_required
def report_json(scan_id):
    scan_row = db.get_scan(scan_id)
    if not scan_row:
        abort(404)
    import json
    scan_row["declarations"] = json.loads(scan_row.pop("declarations_json"))
    return jsonify(scan_row)


# ---------------------------------------------------------------------------
# History / repository search
# ---------------------------------------------------------------------------

@app.route("/history")
@login_required
def history():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip() or None
    rows = db.list_scans(search=q or None, status=status)
    return render_template("history.html", rows=rows, q=q, status=status)


# ---------------------------------------------------------------------------
# Admin: manage officer accounts
# ---------------------------------------------------------------------------

@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    if request.method == "POST":
        db.create_user(request.form["username"], request.form["password"], request.form["role"])
        flash("User created.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_users.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
