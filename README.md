# Legal Metrology Compliance Scanner — Prototype

A working prototype for **"Software System to check compliance of Packaged
Commodities under Legal Metrology (Packaged Commodities) Rules, 2011 by
scanning products, images and labels."**

It scans a photo of a product label, runs OCR, checks the extracted text
against the mandatory declarations in **Rule 6** of the Legal Metrology
(Packaged Commodities) Rules, 2011, estimates whether the net-quantity
declaration meets the **Second Schedule** font-size requirement, and
produces a PDF compliance report — with a dashboard and searchable
repository for enforcement officers.

## 1. What it actually does (tested end-to-end)

| Requirement from the brief | Implemented as |
|---|---|
| Image upload / product scanning | `/scan` — upload form + Pillow image handling |
| Extraction of declarations from labels | `ocr_engine.py` (Tesseract OCR) + `rules_engine.py` (regex detectors) |
| Font size / readability analysis | `rules_engine.check_font_size` using OCR glyph bounding boxes calibrated against a user-supplied Principal Display Panel width |
| Missing / non-compliant declaration detection | Each declaration returns PASS / FAIL / REVIEW with a reason |
| Compliance / non-compliance report generation | `report_pdf.py` (PDF) + `/report/<id>/json` (machine-readable) |
| Repository of scanned products + history | SQLite `scans` table, `/history` search & filter |
| Dashboard for enforcement officials | `/dashboard` — totals, status breakdown, category chart, recent scans |
| Role-based access & authentication | Session login, `officer` / `admin` roles, `@admin_required` on account management |
| Attachment of photographs as evidence | Uploaded image stored and shown on the report + embedded in the PDF |

## 2. Architecture

```
Browser (officer / admin)
        │  HTTPS
        ▼
┌───────────────────────────── Flask app (app.py) ─────────────────────────────┐
│  Auth (session-based)  →  Upload handler  →  OCR  →  Rule engine  →  Report  │
│                                   │                                          │
│                                   ▼                                          │
│                          static/uploads/*.jpg                               │
└───────────────┬───────────────────────────────────────────┬────────────────┘
                │                                            │
                ▼                                            ▼
        ocr_engine.py (pytesseract)                  rules_engine.py
        - text extraction                              - Rule 6 declaration regexes
        - word bounding boxes (px)                      - Second Schedule font table
        - px→mm calibration via PDP width               - PASS/FAIL/REVIEW scoring
                │                                            │
                └─────────────────────┬──────────────────────┘
                                       ▼
                              db.py (SQLite)
                        scans, users tables — repository
                                       │
                                       ▼
                             report_pdf.py (ReportLab)
                          PDF compliance report per scan
```

**Why this split:** `rules_engine.py` has no Flask/OCR dependency, so the
legal logic can be unit-tested and updated independently (e.g. when the
Rules are amended) without touching the web layer or the OCR pipeline.

## 3. Compliance logic (what's automated vs. what needs an officer)

Rule 6(1) declarations checked automatically:
- Name & address of manufacturer/packer/importer — `Rule 6(1)(a)`
- Net quantity in standard units — `Rule 6(1)(c)` / `Rule 8`
- Month & year of manufacture/pack/import — `Rule 6(1)(e)`
- MRP inclusive of all taxes — `Rule 6(1)(f)`
- Consumer care details — `Rule 6(1)(d)`
- Country of origin — conditional (imported goods only) → always marked
  **REVIEW**, never auto-failed, since it doesn't apply to domestically
  made goods
- Net-quantity font size vs. the **Second Schedule** table, *if* the
  officer enters the Principal Display Panel's real-world width/height in
  cm during upload (otherwise this check is marked REVIEW rather than
  guessed)

**Deliberately not auto-decided** (flagged REVIEW, never silently passed):
multi-piece/combination packages, wholesale packages, commodities sold by
number under Rule 2(l), e-commerce-specific disclosures (Rule 6(9)),
exemptions under Rule 26. This is a decision-support tool for officers —
see the disclaimer printed on every PDF report.

## 4. Known limitations of this prototype (be upfront about these)

- **OCR accuracy** depends on photo quality; Tesseract will struggle with
  glare, curved surfaces, low contrast, or stylised fonts. A production
  version should add deskew/perspective correction (OpenCV) and consider
  a layout-aware OCR model for curved/foil labels.
- **Font-size measurement** is an *estimate*: it assumes a front-on photo
  and uses the officer-entered panel width to convert pixels to
  millimetres. It is not a substitute for a physical scale measurement in
  a legal proceeding — hence it's reported as PASS/FAIL with the
  measurement shown, so an officer can double check.
- **Generic name / commodity identification** is not reliably automatable
  from text alone with a rule-based approach and is intentionally left as
  a REVIEW-style manual field in this prototype rather than faked with a
  fragile heuristic.
- Authentication is a minimal session-based system for demo purposes —
  production deployment needs proper password policy, audit logging, and
  likely SSO with the department's existing identity provider.

## 5. Running it locally

```bash
cd legal-metrology-scanner
pip install -r requirements.txt --break-system-packages   # or use a venv
sudo apt-get install tesseract-ocr                          # OCR engine (Linux)
python3 app.py
```

Then open **http://localhost:5050**.

Demo accounts (change these before any real deployment):
- `admin` / `admin123`
- `officer` / `officer123`

Sample label images used during development are in `test_samples/` (synthetic,
generated for testing — not real products).

## 6. Suggested next steps for a full submission

1. Swap SQLite → PostgreSQL and add Alembic migrations for a multi-officer
   deployment.
2. Add a mobile-first capture flow (camera guidance overlay: "align the
   label within the frame") since most field scanning will be on phones.
3. Replace the px→mm font estimate with a reference-object calibration
   (e.g. officer places a ₹5 coin or ID card of known size in frame) to
   remove the manual panel-measurement step.
4. Add an audit trail (who changed what, immutable scan history) for
   evidentiary use.
5. Fine-tune or swap in a layout-aware OCR/vision model trained on Indian
   packaging fonts and formats to raise extraction accuracy beyond
   generic Tesseract.
