import os
import io
import csv
import json
import shutil
import socket
from datetime import datetime
from contextlib import asynccontextmanager
from PIL import Image
from zeroconf.asyncio import AsyncZeroconf
from zeroconf import ServiceInfo

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# --- Config & Directory Setup ---
SECURITY_PIN = "1234"
STORAGE_DIR = "./storage"
ORIGINALS_DIR = os.path.join(STORAGE_DIR, "originals")
THUMBNAILS_DIR = os.path.join(STORAGE_DIR, "thumbnails")
ARCHIVE_DIR = os.path.join(STORAGE_DIR, "archived_jobs")

os.makedirs(ORIGINALS_DIR, exist_ok=True)
os.makedirs(THUMBNAILS_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# In-memory storage dictionary
INCIDENTS = {}


def load_archived_jobs_from_disk():
    """Loads all archived JSON job snapshots from disk into memory on startup."""
    for filename in os.listdir(ARCHIVE_DIR):
        if filename.endswith("_backup.json"):
            file_path = os.path.join(ARCHIVE_DIR, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    job_data = json.load(f)
                    inc_id = filename.replace("_backup.json", "")
                    INCIDENTS[inc_id] = job_data
            except Exception as e:
                print(f"Failed to load archive {filename}: {e}")


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load previously saved jobs from local storage upon boot
    load_archived_jobs_from_disk()

    aiozeroconf = AsyncZeroconf()
    local_ip = get_local_ip()
    info = ServiceInfo(
        "_http._tcp.local.",
        "Emergency Incident Server._http._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=8000,
        properties={"name": "Incident Node"},
        server="incident.local.",
    )
    await aiozeroconf.async_register_service(info)
    yield
    await aiozeroconf.async_unregister_service(info)
    await aiozeroconf.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=STORAGE_DIR), name="media")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def process_image(input_file_path: str, filename: str):
    base_name, _ = os.path.splitext(filename)
    compressed_filename = f"{base_name}.jpg"
    thumb_filename = f"{base_name}_thumb.jpg"

    compressed_path = os.path.join(ORIGINALS_DIR, compressed_filename)
    thumb_path = os.path.join(THUMBNAILS_DIR, thumb_filename)

    with Image.open(input_file_path) as img:
        img = img.convert("RGB")
        img.thumbnail((1200, 1200))
        img.save(compressed_path, "JPEG", optimize=True, quality=70)

        img.thumbnail((300, 300))
        img.save(thumb_path, "JPEG", optimize=True, quality=50)

    if os.path.exists(input_file_path):
        os.remove(input_file_path)

    return f"/media/originals/{compressed_filename}", f"/media/thumbnails/{thumb_filename}"


# --- Auth & Incident Lifecycle Management Endpoints ---

@app.post("/verify_pin")
async def verify_pin(pin: str = Form(...)):
    if pin == SECURITY_PIN:
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid PIN")


@app.get("/incidents")
async def get_incidents():
    return INCIDENTS


@app.post("/create_incident")
async def create_incident(
    title: str = Form(...),
    initial_callsign: str = Form("Command")
):
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    inc_id = f"INC-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    now_str = datetime.now().strftime("%H:%M:%S")

    INCIDENTS[inc_id] = {
        "title": clean_title,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "closed_at": None,
        "status": "Active",
        "logs": [],
        "appliances": {
            initial_callsign.strip(): {
                "status": "On Scene",
                "arrival_time": now_str,
                "last_update": now_str
            }
        }
    }
    await manager.broadcast(json.dumps({"type": "incident_created", "incident_id": inc_id}))
    return {"status": "success", "incident_id": inc_id, "data": INCIDENTS[inc_id]}


@app.post("/finish_incident")
async def finish_incident(incident_id: str = Form(...)):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")

    inc = INCIDENTS[incident_id]
    if inc["status"] == "Completed":
        return {"status": "already_closed"}

    inc["status"] = "Completed"
    inc["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Mark all active appliances as Departed
    for app_details in inc["appliances"].values():
        app_details["status"] = "Departed"
        app_details["last_update"] = datetime.now().strftime("%H:%M:%S")

    # Persist JSON backup snapshot to local disk
    backup_file_path = os.path.join(ARCHIVE_DIR, f"{incident_id}_backup.json")
    with open(backup_file_path, "w", encoding="utf-8") as f:
        json.dump(inc, f, indent=2)

    await manager.broadcast(json.dumps({"type": "incident_finished", "incident_id": incident_id}))
    return {"status": "success", "incident_id": incident_id, "backup_path": backup_file_path}


# --- Scoped Incident Logging & Tracking Endpoints ---

@app.get("/logs")
async def get_logs(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    return INCIDENTS[incident_id]["logs"]


@app.get("/appliances")
async def get_appliances(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    return INCIDENTS[incident_id]["appliances"]


@app.post("/register_appliance")
async def register_appliance(incident_id: str = Form(...), callsign: str = Form(...), initial_status: str = Form("On Scene")):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    if INCIDENTS[incident_id]["status"] == "Completed":
        raise HTTPException(status_code=400, detail="Cannot alter completed job.")

    clean_callsign = callsign.strip()
    if not clean_callsign:
        raise HTTPException(status_code=400, detail="Callsign cannot be empty")

    now_str = datetime.now().strftime("%H:%M:%S")
    appliances = INCIDENTS[incident_id]["appliances"]
    if clean_callsign not in appliances:
        appliances[clean_callsign] = {
            "status": initial_status,
            "arrival_time": now_str,
            "last_update": now_str
        }
        await manager.broadcast(json.dumps({"type": "appliance_registered", "incident_id": incident_id, "callsign": clean_callsign}))

    return {"status": "success", "appliances": appliances}


@app.post("/update_appliance_status")
async def update_appliance_status(incident_id: str = Form(...), callsign: str = Form(...), new_status: str = Form(...)):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    if INCIDENTS[incident_id]["status"] == "Completed":
        raise HTTPException(status_code=400, detail="Cannot alter completed job.")

    clean_callsign = callsign.strip()
    appliances = INCIDENTS[incident_id]["appliances"]
    if clean_callsign not in appliances:
        raise HTTPException(status_code=404, detail="Appliance not registered")

    now_str = datetime.now().strftime("%H:%M:%S")
    appliances[clean_callsign]["status"] = new_status
    appliances[clean_callsign]["last_update"] = now_str

    await manager.broadcast(json.dumps({
        "type": "status_change",
        "incident_id": incident_id,
        "callsign": clean_callsign,
        "status": new_status,
        "time": now_str
    }))
    return {"status": "success", "appliances": appliances}


@app.post("/upload/")
async def upload_entry(
    incident_id: str = Form(...),
    responder_id: str = Form(...),
    note: str = Form(...),
    tag: str = Form("General"),
    file: UploadFile = File(...)
):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")
    if INCIDENTS[incident_id]["status"] == "Completed":
        raise HTTPException(status_code=400, detail="Cannot upload to completed job.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_path = os.path.join(STORAGE_DIR, f"temp_{timestamp}_{file.filename}")

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    full_url, thumb_url = process_image(temp_path, f"{timestamp}_{file.filename}")

    logs = INCIDENTS[incident_id]["logs"]
    payload = {
        "id": len(logs) + 1,
        "responder": responder_id,
        "note": note,
        "tag": tag,
        "full_image": full_url,
        "thumbnail": thumb_url,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }

    logs.insert(0, payload)
    await manager.broadcast(json.dumps({"type": "new_log", "incident_id": incident_id, "data": payload}))
    return {"status": "success", "data": payload}


# --- CSV & PDF Reporting Endpoints ---

@app.get("/export/logs/csv")
async def export_logs_csv(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "Appliance/Callsign", "Category/Tag", "Note", "Image Path"])

    for log in INCIDENTS[incident_id]["logs"]:
        writer.writerow([
            log.get("id"),
            log.get("timestamp"),
            log.get("responder"),
            log.get("tag"),
            log.get("note"),
            log.get("full_image")
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=logs_{incident_id}.csv"}
    )


@app.get("/export/appliances/csv")
async def export_appliances_csv(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Callsign", "Status", "Arrival Time", "Last Status Update"])

    for callsign, details in INCIDENTS[incident_id]["appliances"].items():
        writer.writerow([
            callsign,
            details.get("status"),
            details.get("arrival_time"),
            details.get("last_update")
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=appliances_{incident_id}.csv"}
    )


@app.get("/export/summary/pdf")
async def export_summary_pdf(incident_id: str):
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=404, detail="Incident not found")

    inc_data = INCIDENTS[incident_id]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"<b>INCIDENT ACTION SUMMARY: {inc_data['title']} ({incident_id})</b>", styles['Title']))
    story.append(Paragraph(f"Status: {inc_data['status']} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 15))

    story.append(Paragraph("<b>Responding Appliance Tracking</b>", styles['Heading2']))
    app_data = [["Callsign", "Status", "Arrival Time", "Last Update"]]
    for callsign, details in inc_data["appliances"].items():
        app_data.append([
            callsign,
            details.get("status", ""),
            details.get("arrival_time", ""),
            details.get("last_update", "")
        ])

    app_table = Table(app_data, colWidths=[130, 120, 120, 120])
    app_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(app_table)
    story.append(Spacer(1, 20))

    story.append(Paragraph("<b>Incident Event Log</b>", styles['Heading2']))
    log_data = [["Time", "Callsign", "Tag", "Observation / Note"]]
    for log in inc_data["logs"]:
        log_data.append([
            log.get("timestamp", ""),
            log.get("responder", ""),
            log.get("tag", ""),
            Paragraph(log.get("note", ""), styles['Normal'])
        ])

    log_table = Table(log_data, colWidths=[60, 90, 90, 250])
    log_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(log_table)

    doc.build(story)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=summary_{incident_id}.pdf"}
    )