import os
import io
import csv
import json
from datetime import datetime
from PIL import Image, ImageOps

import streamlit as st
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

STATUS_BADGES = {
    "On Scene": "🟢 On Scene",
    "Staging": "🟡 Staging",
    "Departed": "🔴 Departed"
}

st.set_page_config(
    page_title="Incident Response Operations System",
    page_icon="🚨",
    layout="wide"
)

# --- Persistent State Initialization ---
if "pin_authenticated" not in st.session_state:
    st.session_state.pin_authenticated = False
if "selected_incident" not in st.session_state:
    st.session_state.selected_incident = None
if "incidents" not in st.session_state:
    st.session_state.incidents = {}

# Restore archived incidents from disk on startup
if "restored_disk" not in st.session_state:
    if os.path.exists(ARCHIVE_DIR):
        for filename in os.listdir(ARCHIVE_DIR):
            if filename.endswith("_backup.json"):
                file_path = os.path.join(ARCHIVE_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        inc_id = filename.replace("_backup.json", "")
                        st.session_state.incidents[inc_id] = data
                except Exception:
                    pass
    st.session_state.restored_disk = True


# --- Core Helper Functions ---

def render_branding_header():
    """Renders top banner header with attribution credit."""
    st.markdown(
        """
        <div style="background-color: #1e293b; padding: 18px; border-radius: 8px; margin-bottom: 20px; color: white;">
            <h2 style="margin: 0; color: #f8fafc;">🚨 Incident Response Operations System</h2>
            <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 0.9em;">Operational Dispatch & Field Resource Command Terminal</p>
        </div>
        """,
        unsafe_allow_html=True
    )


def process_and_save_image(uploaded_file):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{timestamp}_{uploaded_file.name}"
    compressed_filename = f"{base_name}.jpg"
    thumb_filename = f"{base_name}_thumb.jpg"

    compressed_path = os.path.join(ORIGINALS_DIR, compressed_filename)
    thumb_path = os.path.join(THUMBNAILS_DIR, thumb_filename)

    image_bytes = uploaded_file.getvalue()
    with Image.open(io.BytesIO(image_bytes)) as img:
        # Auto-rotate image based on EXIF camera orientation metadata
        img = ImageOps.exif_transpose(img)
        
        img = img.convert("RGB")
        
        # Save main compressed image
        main_img = img.copy()
        main_img.thumbnail((1200, 1200))
        main_img.save(compressed_path, "JPEG", optimize=True, quality=70)

        # Save thumbnail
        thumb_img = img.copy()
        thumb_img.thumbnail((300, 300))
        thumb_img.save(thumb_path, "JPEG", optimize=True, quality=50)

    return compressed_path, thumb_path


def generate_pdf_bytes(inc_id):
    inc_data = st.session_state.incidents[inc_id]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"<b>INCIDENT ACTION SUMMARY: {inc_data['title']} ({inc_id})</b>", styles['Title']))
    story.append(Paragraph(f"Status: {inc_data['status']} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 15))

    story.append(Paragraph("<b>Responding Appliance Tracking</b>", styles['Heading2']))
    app_data = [["Callsign", "Status", "Arrival Time", "Last Update"]]
    for callsign, details in inc_data["appliances"].items():
        app_data.append([callsign, details.get("status", ""), details.get("arrival_time", ""), details.get("last_update", "")])

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
        log_data.append([log.get("timestamp", ""), log.get("responder", ""), log.get("tag", ""), Paragraph(log.get("note", ""), styles['Normal'])])

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
    return buffer.getvalue()


def generate_csv_logs(inc_id):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "Appliance/Callsign", "Category/Tag", "Note", "Full Image Path"])
    for log in st.session_state.incidents[inc_id]["logs"]:
        writer.writerow([log.get("id"), log.get("timestamp"), log.get("responder"), log.get("tag"), log.get("note"), log.get("full_image")])
    return output.getvalue().encode('utf-8')


# --- UI VIEWS ---

def show_landing_page():
    render_branding_header()
    
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("🔒 Security Authentication")
        st.caption("Enter Station PIN to access active operations terminal")
        with st.form("pin_form"):
            pin_input = st.text_input("Station Security PIN", type="password")
            if st.form_submit_button("Authenticate Access", use_container_width=True):
                if pin_input == SECURITY_PIN:
                    st.session_state.pin_authenticated = True
                    st.success("Access Granted!")
                    st.rerun()
                else:
                    st.error("Invalid Security PIN")
                    
    with col2:
        st.info("ℹ️ **Default Security PIN:** `1234`")
        st.caption("📸 **System Cover Image:** *Incident Response Operations System* — Source: Popcorn Arts / Getty Images")


def show_incident_selector():
    render_branding_header()

    incidents = st.session_state.incidents
    active_incidents = {k: v for k, v in incidents.items() if v.get("status") == "Active"}
    completed_incidents = {k: v for k, v in incidents.items() if v.get("status") == "Completed"}

    col1, col2 = st.columns([2, 1])

    with col1:
        tab_active, tab_archived = st.tabs(["🔥 Active Jobs", "📁 Archived Jobs Log"])

        with tab_active:
            if not active_incidents:
                st.info("No active incidents. Initialize a job using the panel on the right.")
            else:
                for inc_id, details in active_incidents.items():
                    with st.container(border=True):
                        c1, c2 = st.columns([3, 1])
                        with c1:
                            st.markdown(f"### {details['title']}")
                            st.caption(f"ID: `{inc_id}` | Created: {details['created_at']}")
                        with c2:
                            if st.button("Log On to Job", key=f"join_{inc_id}", use_container_width=True):
                                st.session_state.selected_incident = inc_id
                                st.rerun()

        with tab_archived:
            if not completed_incidents:
                st.info("No completed jobs logged.")
            else:
                for inc_id, details in completed_incidents.items():
                    with st.expander(f"🏁 {details['title']} ({inc_id}) — Closed: {details.get('closed_at', 'N/A')}"):
                        st.markdown(f"**Logs Recorded:** `{len(details.get('logs', []))}` entries")
                        st.markdown("#### Event Logs")
                        for log in details.get("logs", []):
                            st.markdown(f"- **{log['timestamp']}** [`{log['responder']}`] *{log['tag']}*: {log['note']}")

                        st.divider()
                        col_pdf, col_csv = st.columns(2)
                        with col_pdf:
                            st.download_button("📥 PDF Report", data=generate_pdf_bytes(inc_id), file_name=f"summary_{inc_id}.pdf", mime="application/pdf", key=f"pdf_{inc_id}")
                        with col_csv:
                            st.download_button("📊 Logs CSV", data=generate_csv_logs(inc_id), file_name=f"logs_{inc_id}.csv", mime="text/csv", key=f"csv_{inc_id}")

    with col2:
        st.subheader("Appliance Job Initialization")
        with st.form("create_inc_form"):
            inc_title = st.text_input("Incident Location / Title", placeholder="e.g. Structure Fire - 42 Main St")
            initial_unit = st.text_input("Initial Appliance Callsign", value="Engine 1")

            if st.form_submit_button("Initialize & On Scene", use_container_width=True):
                if not inc_title.strip() or not initial_unit.strip():
                    st.warning("All fields are required.")
                else:
                    inc_id = f"INC-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    now_str = datetime.now().strftime("%H:%M:%S")

                    st.session_state.incidents[inc_id] = {
                        "title": inc_title.strip(),
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "closed_at": None,
                        "status": "Active",
                        "logs": [],
                        "appliances": {
                            initial_unit.strip(): {
                                "status": "On Scene",
                                "arrival_time": now_str,
                                "last_update": now_str
                            }
                        }
                    }
                    st.session_state.selected_incident = inc_id
                    st.success(f"Incident {inc_id} Initialized!")
                    st.rerun()


def show_incident_feed():
    inc_id = st.session_state.selected_incident
    current_job = st.session_state.incidents[inc_id]

    st.title(f"🚨 {current_job.get('title', 'Incident Feed')}")
    st.caption(f"Incident ID: `{inc_id}`")

    with st.sidebar:
        st.header("Submit Report")
        appliances_data = current_job.get("appliances", {})
        active_callsigns = [c for c, d in appliances_data.items() if d["status"] != "Departed"] or ["Command"]

        responder_id = st.selectbox("Select Appliance / Callsign", options=active_callsigns)
        tag = st.selectbox("Category", ["#HazMat", "#SearchAndRescue", "#Medical", "#Infrastructure", "General"])
        note = st.text_area("Incident Note / Observation")
        uploaded_file = st.file_uploader("Take Photo or Upload Image", type=["jpg", "png", "jpeg"])

        if st.button("Transmit Update", use_container_width=True):
            if not note or not uploaded_file:
                st.error("Please provide both a note and an image.")
            else:
                full_path, thumb_path = process_and_save_image(uploaded_file)
                payload = {
                    "id": len(current_job["logs"]) + 1,
                    "responder": responder_id,
                    "note": note,
                    "tag": tag,
                    "full_image": full_path,
                    "thumbnail": thumb_path,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                }
                current_job["logs"].insert(0, payload)
                st.success("Transmitted successfully!")
                st.rerun()

    st.subheader("Live Log")
    logs = current_job.get("logs", [])
    if not logs:
        st.info("No reports logged yet for this incident.")

    for item in logs:
        with st.container(border=True):
            col1, col2 = st.columns([1, 3])
            
            with col1:
                # Render thumbnail in column
                if os.path.exists(item.get('thumbnail', '')):
                    st.image(item['thumbnail'], use_container_width=True)
                elif os.path.exists(item.get('full_image', '')):
                    st.image(item['full_image'], use_container_width=True)

            with col2:
                st.markdown(f"**{item['responder']}** `{item['tag']}` — *{item['timestamp']}*")
                st.write(item['note'])

                # High-Res Image Expander View
                if os.path.exists(item.get('full_image', '')):
                    with st.expander("🔍 View High-Res Image"):
                        st.image(item['full_image'], caption=f"Full Resolution Image (Logged by {item['responder']} at {item['timestamp']})", use_container_width=True)


def show_appliance_checkin():
    inc_id = st.session_state.selected_incident
    current_job = st.session_state.incidents[inc_id]

    st.title(f"🚒 Appliance Tracking: {current_job.get('title', '')}")
    st.caption(f"Incident ID: `{inc_id}`")

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Register Arriving Unit")
        new_callsign = st.text_input("Appliance Callsign (e.g., Engine 1, HazMat 2)")
        initial_status = st.selectbox("Initial Status", ["On Scene", "Staging"])

        if st.button("Log On to Incident", use_container_width=True):
            if not new_callsign.strip():
                st.warning("Enter a valid callsign.")
            else:
                now_str = datetime.now().strftime("%H:%M:%S")
                current_job["appliances"][new_callsign.strip()] = {
                    "status": initial_status,
                    "arrival_time": now_str,
                    "last_update": now_str
                }
                st.success(f"Appliance '{new_callsign.strip()}' registered!")
                st.rerun()

    with col2:
        st.subheader("Active Resources Summary")
        for callsign, details in list(current_job["appliances"].items()):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 2])
                with c1:
                    st.markdown(f"### {callsign}")
                    st.caption(f"Arrived: `{details['arrival_time']}`")
                with c2:
                    st.markdown(f"**Status:** {STATUS_BADGES.get(details['status'], details['status'])}")
                    st.caption(f"Updated: `{details['last_update']}`")
                with c3:
                    new_st = st.selectbox("Update Status", options=["Staging", "On Scene", "Departed"], index=["Staging", "On Scene", "Departed"].index(details['status']), key=f"status_select_{callsign}")
                    if new_st != details['status']:
                        details['status'] = new_st
                        details['last_update'] = datetime.now().strftime("%H:%M:%S")
                        st.rerun()


def show_export_page():
    inc_id = st.session_state.selected_incident
    current_job = st.session_state.incidents[inc_id]

    st.title(f"📄 Incident Exports: {current_job.get('title', '')}")
    st.caption(f"Incident ID: `{inc_id}`")

    st.subheader("1. Formal PDF Incident Summary")
    st.download_button("📥 Download PDF Summary Report", data=generate_pdf_bytes(inc_id), file_name=f"summary_{inc_id}.pdf", mime="application/pdf", use_container_width=True)

    st.divider()

    st.subheader("2. Raw Data Exports (CSV)")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button("📊 Download Incident Logs (CSV)", data=generate_csv_logs(inc_id), file_name=f"logs_{inc_id}.csv", mime="text/csv", use_container_width=True)
    with col2:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Callsign", "Status", "Arrival Time", "Last Status Update"])
        for callsign, details in current_job["appliances"].items():
            writer.writerow([callsign, details.get("status"), details.get("arrival_time"), details.get("last_update")])
        st.download_button("🚒 Download Appliance History (CSV)", data=output.getvalue().encode('utf-8'), file_name=f"appliances_{inc_id}.csv", mime="text/csv", use_container_width=True)


# --- ROUTER ---

def main():
    if not st.session_state.pin_authenticated:
        show_landing_page()
        return

    st.sidebar.markdown(f"🔐 **PIN Authenticated**")

    if st.session_state.selected_incident:
        inc_id = st.session_state.selected_incident
        st.sidebar.info(f"Active Job: **{inc_id}**")

        if st.sidebar.button("🏁 Finish Job & Save Logs", type="primary", use_container_width=True):
            inc = st.session_state.incidents[inc_id]
            inc["status"] = "Completed"
            inc["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for app_details in inc["appliances"].values():
                app_details["status"] = "Departed"
                app_details["last_update"] = datetime.now().strftime("%H:%M:%S")

            # Persist JSON backup snapshot to local disk
            backup_file_path = os.path.join(ARCHIVE_DIR, f"{inc_id}_backup.json")
            with open(backup_file_path, "w", encoding="utf-8") as f:
                json.dump(inc, f, indent=2)

            st.sidebar.success("Job closed & snapshot saved!")
            st.session_state.selected_incident = None
            st.rerun()

        if st.sidebar.button("🔄 Switch Job (Keep Active)", use_container_width=True):
            st.session_state.selected_incident = None
            st.rerun()

    if st.sidebar.button("🔒 Log Out", use_container_width=True):
        st.session_state.pin_authenticated = False
        st.session_state.selected_incident = None
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("📷 *Cover Art:* Incident Response Operations System (Popcorn Arts / Getty Images)")

    if not st.session_state.selected_incident:
        show_incident_selector()
        return

    pg = st.navigation([
        st.Page(show_incident_feed, title="Incident Feed", icon="🚨"),
        st.Page(show_appliance_checkin, title="Appliance Tracking", icon="🚒"),
        st.Page(show_export_page, title="Exports & Reports", icon="📄"),
    ])
    pg.run()


if __name__ == "__main__":
    main()