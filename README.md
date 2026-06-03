# CandidateConnect-Field-DEV

Separate Streamlit app for Candidate Connect mobile/field use.

## Repo structure

```text
CandidateConnect-Field-DEV/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── secrets.toml.template
```

## What this app does

- Uses the same Candidate Connect DEV R2 bucket and security store as the web app.
- Lets field users log in separately from the main web app.
- Reads assignments from:

```text
app_state/mobile_assignments/<campaign_id>.json
```

- Stores/syncs staged field results to:

```text
app_state/mobile_results/<campaign_id>.json
```

- Does **not** update voter records yet.
- Does **not** share Streamlit sidebar/session state with the main web app.

## Streamlit Cloud deployment

Create a new Streamlit app from this repo and set:

```text
Main file path: app.py
```

Copy the DEV secrets from the main CandidateConnect-DEV Streamlit app into this new app's Streamlit secrets.

Do not commit `.streamlit/secrets.toml` to GitHub.
