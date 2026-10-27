# Frontend — Mailbox Detection Studio

Static React UI (Babel-in-browser). Served by the backend at **http://localhost:8002** by default.

Do not start this folder alone for the full pipeline — use `..\start_app.bat`.

## Pipeline stages (wired to backend Agents 1–7)

1. Input + Dedup  
2. Parcel  
3. Street View Images  
4. Image Enhancement  
5. Detect Mailboxes  
6. Reject Neighbour Mailboxes  
7. Export Results  

API base: same origin `/api/belgium/*` (see `assets/api/belgium-api.js`).
