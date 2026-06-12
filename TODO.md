# TODO

## Interview preparation

Extend the toolkit from "apply" into "prepare." The application question bank
(`data/application_bank.yaml` + `answers.py`) is the first step. Planned next:

- A behavioral-question bank (STAR-format stories) keyed by competency, reusing
  the same YAML + `answers.py` lookup pattern.
- A technical-question bank per role/skill (e.g. ML fundamentals, SQL, system design).
- Optional: per-job prep sheets that pull the tailored resume bullets + the most
  relevant stored answers for a given posting into one cheat sheet.

Keep the storage format consistent with `application_bank.yaml` (list of entries
with `id`, `question`, `answer`, `tags`) so one lookup tool serves all banks.

---

## Drive upload of generated PDFs

Currently, after `tailor.py` builds the PDFs, they stay in `output/` locally.
Uploading them to a Drive folder requires passing binary content as base64
through the Drive MCP, which is impractical for files larger than ~10KB.

**Option A — google-api-python-client (recommended)**
Use a service account or stored OAuth token to upload directly:

```python
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
service = build('drive', 'v3', credentials=creds)
service.files().create(
    body={'name': filename, 'parents': [FOLDER_ID]},
    media_body=MediaFileUpload(path, mimetype='application/pdf'),
).execute()
```

Add `upload_to_drive(pdf_path, folder_id)` to `tailor.py` as an optional step
gated on a `--upload` flag.

**Option B — manual upload**
Drag the generated PDFs from `output/{job_id}/` to your Drive folder.
Simple workaround until Option A is implemented.

---

## Google Sheet write-back

Currently, after a resume is tailored and the PDF is uploaded to Drive, the
**jobs spreadsheet is not updated** (e.g. the Status or Notes columns).
This is a known limitation of the Google Drive MCP, which has no cell-level
write API (`create_file` creates new files; it cannot patch existing sheet rows).

### Options to close this gap

**Option A — Google Sheets API (recommended for full automation)**
Add a service account or OAuth flow using `gspread` + `google-auth`.
Allows reading and writing individual cells programmatically.
Requires a Google Cloud project and credentials JSON.

```bash
pip install gspread google-auth
```

`tailor.py` would call `sheet.update_cell(row, col, value)` after building the PDF
to stamp the Status or add a note.

**Option B — Claude replaces the whole spreadsheet**
Drive MCP `create_file` with `text/csv` content can overwrite (or create a new version of)
the spreadsheet. Risky for large sheets; loses formatting and comments.
Suitable only as a short-term workaround for small tracking sheets.

**Option C — Local CSV mirror**
Maintain a `data/jobs.csv` alongside the sheet as the authoritative local record.
`tailor.py` writes back to the CSV; the sheet is updated manually or via a periodic sync.
Low friction, no extra auth, but the sheet and CSV can drift.

**Option D — Google Apps Script trigger**
A lightweight Apps Script in the sheet watches for a new row and calls a webhook
(e.g. a Cloud Run endpoint or ngrok tunnel) that triggers `tailor.py` on your machine.
Fully automated intake, but requires a persistent listener and some cloud glue.

### Recommended path

Start with **Option A** once you have a Google Cloud project. Add `gspread` to
`requirements.txt` and a `--write-back` flag to `tailor.py` that updates the
Status column to `Applied` and stamps the Resume File name after a successful build.
