# Gmail demo intake packet assets

Place the real blank DOCX files for the Gmail patient demo in this folder.

Expected filenames:

- `privacy_notice_acknowledged.docx`
- `telehealth_consent.docx`
- `clinical_intake_form.docx`
- `pre_session_screening_questionnaire.docx`

`POST /api/demo/gmail-patient/reset` registers these files as the active blank
intake packet attachments for the demo intake template.
