import streamlit as st
import requests

SERVER_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Emergency Incident Node", layout="wide")

STATUS_BADGES = {
    "On Scene": "🟢 On Scene",
    "Staging": "🟡 Staging",
    "Departed": "🔴 Departed"
}

if "pin_authenticated" not in st.session_state:
    st.session_state.pin_authenticated = False
if "selected_incident" not in st.session_state:
    st.session_state.selected_incident = None


def fetch_incidents():
    try:
        res = requests.get(f"{SERVER_URL}/incidents", timeout=3)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return {}


def fetch_appliances_data(inc_id):
    try:
        res = requests.get(f"{SERVER_URL}/appliances", params={"incident_id": inc_id}, timeout=3)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return {}


# --- UNAUTHENTICATED: Gateway Page ---
def show_landing_page():
    st.title("🔒 Emergency Incident Gateway")
    st.caption("Enter PIN to access local command operations")

    col1, col2 = st.columns([1, 1])

    with col1:
        with st.form("pin_form"):
            pin_input = st.text_input("Enter Station Security PIN", type="password")
            submit_pin = st.form_submit_button("Authenticate Access", use_container_width=True)

            if submit_pin:
                try:
                    res = requests.post(f"{SERVER_URL}/verify_pin", data={"pin": pin_input}, timeout=3)
                    if res.status_code == 200:
                        st.session_state.pin_authenticated = True
                        st.success("Access Granted!")
                        st.rerun()
                    else:
                        st.error("Invalid Security PIN")
                except Exception as e:
                    st.error(f"Cannot connect to server: {e}")

    with col2:
        st.info("ℹ️ **Default PIN:** `1234` (Configurable in `app.py`)")


# --- AUTHENTICATED: Portal Page ---
def show_incident_selector():
    st.title("🚨 Emergency Incident Portal")
    st.caption("Initialize a new incident or attach to an active/archived job")

    incidents = fetch_incidents()
    active_incidents = {k: v for k, v in incidents.items() if v.get("status") == "Active"}
    completed_incidents = {k: v for k, v in incidents.items() if v.get("status") == "Completed"}

    col1, col2 = st.columns([2, 1])

    with col1:
        tab_active, tab_archived = st.tabs(["🔥 Active Jobs", "📁 Archived / Recent Jobs Log"])

        with tab_active:
            if not active_incidents:
                st.info("No active incidents currently logged. Initialize a job using the panel on the right.")
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
                st.info("No completed jobs logged in this session.")
            else:
                for inc_id, details in completed_incidents.items():
                    with st.expander(f"🏁 {details['title']} ({inc_id}) — Closed: {details.get('closed_at', 'N/A')}"):
                        st.markdown(f"**Logs Recorded:** `{len(details.get('logs', []))}` entries")
                        st.markdown(f"**Units Registered:** `{len(details.get('appliances', {}))}` appliances")

                        st.markdown("#### Event Logs")
                        for log in details.get("logs", []):
                            st.markdown(f"- **{log['timestamp']}** [`{log['responder']}`] *{log['tag']}*: {log['note']}")

                        st.divider()
                        st.markdown("#### Quick Reports")
                        col_pdf, col_csv = st.columns(2)
                        with col_pdf:
                            pdf_res = requests.get(f"{SERVER_URL}/export/summary/pdf", params={"incident_id": inc_id})
                            if pdf_res.status_code == 200:
                                st.download_button("📥 PDF Report", data=pdf_res.content, file_name=f"summary_{inc_id}.pdf", mime="application/pdf", key=f"pdf_{inc_id}")
                        with col_csv:
                            csv_res = requests.get(f"{SERVER_URL}/export/logs/csv", params={"incident_id": inc_id})
                            if csv_res.status_code == 200:
                                st.download_button("📊 Logs CSV", data=csv_res.content, file_name=f"logs_{inc_id}.csv", mime="text/csv", key=f"csv_{inc_id}")

    with col2:
        st.subheader("Appliance Job Initialization")
        with st.form("create_inc_form"):
            inc_title = st.text_input("Incident Location / Title", placeholder="e.g. Structure Fire - 42 Main St")
            initial_unit = st.text_input("Initial Appliance Callsign", value="Engine 1")
            create_sub = st.form_submit_button("Initialize & On Scene", use_container_width=True)

            if create_sub:
                if not inc_title.strip():
                    st.warning("Incident title is required.")
                elif not initial_unit.strip():
                    st.warning("Appliance callsign is required.")
                else:
                    try:
                        res = requests.post(
                            f"{SERVER_URL}/create_incident",
                            data={"title": inc_title.strip(), "initial_callsign": initial_unit.strip()},
                            timeout=3
                        )
                        if res.status_code == 200:
                            new_id = res.json()["incident_id"]
                            st.session_state.selected_incident = new_id
                            st.success(f"Incident {new_id} Initialized by {initial_unit.strip()}!")
                            st.rerun()
                        else:
                            st.error("Failed to initialize incident.")
                    except Exception as e:
                        st.error(f"Server connection error: {e}")


# --- ACTIVE WORKFLOWS ---

def show_incident_feed():
    inc_id = st.session_state.selected_incident
    incidents = fetch_incidents()
    current_job = incidents.get(inc_id, {})

    st.title(f"🚨 {current_job.get('title', 'Incident Feed')}")
    st.caption(f"Incident ID: `{inc_id}` | Real-time field updates")

    with st.sidebar:
        st.header("Submit Report")
        appliances_data = fetch_appliances_data(inc_id)

        active_callsigns = [
            callsign for callsign, details in appliances_data.items()
            if details["status"] != "Departed"
        ]
        if not active_callsigns:
            active_callsigns = ["Command"]

        responder_id = st.selectbox("Select Appliance / Callsign", options=active_callsigns)
        tag = st.selectbox("Category", ["#HazMat", "#SearchAndRescue", "#Medical", "#Infrastructure", "General"])
        note = st.text_area("Incident Note / Observation")
        uploaded_file = st.file_uploader("Take Photo or Upload Image", type=["jpg", "png", "jpeg"])

        if st.button("Transmit Update", use_container_width=True):
            if not note or not uploaded_file:
                st.error("Please provide both a note and an image.")
            else:
                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                data = {"incident_id": inc_id, "responder_id": responder_id, "note": note, "tag": tag}

                try:
                    res = requests.post(f"{SERVER_URL}/upload/", data=data, files=files, timeout=5)
                    if res.status_code == 200:
                        st.success("Transmitted successfully!")
                        st.rerun()
                    else:
                        st.error(f"Failed to post report. HTTP {res.status_code}")
                except Exception as e:
                    st.error(f"Could not connect to server: {e}")

    # Main Feed Display
    st.subheader("Live Log")
    try:
        response = requests.get(f"{SERVER_URL}/logs", params={"incident_id": inc_id}, timeout=3)
        if response.status_code == 200:
            logs = response.json()
            if not logs:
                st.info("No reports logged yet for this incident.")

            for item in logs:
                with st.container(border=True):
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.image(f"{SERVER_URL}{item['thumbnail']}", use_container_width=True)
                    with col2:
                        st.markdown(f"**{item['responder']}** `{item['tag']}` — *{item['timestamp']}*")
                        st.write(item['note'])
                        st.markdown(f"[🔍 View High-Res Image]({SERVER_URL}{item['full_image']})")
    except Exception:
        st.warning("Connecting to incident node...")


def show_appliance_checkin():
    inc_id = st.session_state.selected_incident
    incidents = fetch_incidents()
    current_job = incidents.get(inc_id, {})

    st.title(f"🚒 Appliance Tracking: {current_job.get('title', '')}")
    st.caption(f"Incident ID: `{inc_id}` | Unit check-ins and operational status")

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Register Arriving Unit")
        new_callsign = st.text_input("Appliance Callsign (e.g., Engine 1, HazMat 2)")
        initial_status = st.selectbox("Initial Status", ["On Scene", "Staging"])

        if st.button("Log On to Incident", use_container_width=True):
            if not new_callsign.strip():
                st.warning("Please enter a valid callsign.")
            else:
                try:
                    res = requests.post(
                        f"{SERVER_URL}/register_appliance",
                        data={"incident_id": inc_id, "callsign": new_callsign.strip(), "initial_status": initial_status},
                        timeout=3
                    )
                    if res.status_code == 200:
                        st.success(f"Appliance '{new_callsign.strip()}' registered!")
                        st.rerun()
                    else:
                        st.error("Failed to register appliance.")
                except Exception as e:
                    st.error(f"Error connecting to server: {e}")

    with col2:
        st.subheader("Active Resources Summary")
        appliances_data = fetch_appliances_data(inc_id)

        for callsign, details in list(appliances_data.items()):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 2])

                with c1:
                    st.markdown(f"### {callsign}")
                    st.caption(f"Arrived at: `{details['arrival_time']}`")

                with c2:
                    current_badge = STATUS_BADGES.get(details['status'], details['status'])
                    st.markdown(f"**Status:** {current_badge}")
                    st.caption(f"Updated: `{details['last_update']}`")

                with c3:
                    new_st = st.selectbox(
                        "Update Status",
                        options=["Staging", "On Scene", "Departed"],
                        index=["Staging", "On Scene", "Departed"].index(details['status']),
                        key=f"status_select_{callsign}"
                    )

                    if new_st != details['status']:
                        try:
                            requests.post(
                                f"{SERVER_URL}/update_appliance_status",
                                data={"incident_id": inc_id, "callsign": callsign, "new_status": new_st},
                                timeout=3
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to update status: {e}")


def show_export_page():
    inc_id = st.session_state.selected_incident
    incidents = fetch_incidents()
    current_job = incidents.get(inc_id, {})

    st.title(f"📄 Incident Exports: {current_job.get('title', '')}")
    st.caption(f"Incident ID: `{inc_id}` | Export records for post-incident reporting")

    st.subheader("1. Formal PDF Incident Summary")
    st.write("Generates a formatted PDF containing resource tracking logs and chronological operational entries.")

    try:
        pdf_res = requests.get(f"{SERVER_URL}/export/summary/pdf", params={"incident_id": inc_id}, timeout=5)
        if pdf_res.status_code == 200:
            st.download_button(
                label="📥 Download PDF Summary Report",
                data=pdf_res.content,
                file_name=f"summary_{inc_id}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
    except Exception as e:
        st.error(f"Could not connect to export service: {e}")

    st.divider()

    st.subheader("2. Raw Data Exports (CSV)")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### Incident Log Entries")
        try:
            logs_csv = requests.get(f"{SERVER_URL}/export/logs/csv", params={"incident_id": inc_id}, timeout=5)
            if logs_csv.status_code == 200:
                st.download_button(
                    label="📊 Download Incident Logs (CSV)",
                    data=logs_csv.content,
                    file_name=f"logs_{inc_id}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        except Exception:
            st.error("Error loading CSV export.")

    with col2:
        st.markdown("##### Appliance History")
        try:
            app_csv = requests.get(f"{SERVER_URL}/export/appliances/csv", params={"incident_id": inc_id}, timeout=5)
            if app_csv.status_code == 200:
                st.download_button(
                    label="🚒 Download Appliance History (CSV)",
                    data=app_csv.content,
                    file_name=f"appliances_{inc_id}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        except Exception:
            st.error("Error loading CSV export.")


# --- Master Router ---
def main():
    if not st.session_state.pin_authenticated:
        show_landing_page()
        return

    st.sidebar.markdown(f"🔐 **PIN Authenticated**")

    # --- LOG OUT BUTTON ---
    if st.sidebar.button("🔒 Log Out", use_container_width=True):
        st.session_state.pin_authenticated = False
        st.session_state.selected_incident = None
        st.rerun()

    st.sidebar.divider()

    if st.session_state.selected_incident:
        inc_id = st.session_state.selected_incident
        st.sidebar.info(f"Active Job: **{inc_id}**")

        if st.sidebar.button("🏁 Finish Job & Save Logs", type="primary", use_container_width=True):
            try:
                res = requests.post(f"{SERVER_URL}/finish_incident", data={"incident_id": inc_id}, timeout=5)
                if res.status_code == 200:
                    st.sidebar.success("Job closed & snapshot saved!")
                    st.session_state.selected_incident = None
                    st.rerun()
                else:
                    st.sidebar.error("Failed to close job.")
            except Exception as e:
                st.sidebar.error(f"Connection error: {e}")

        if st.sidebar.button("🔄 Switch Job (Keep Active)", use_container_width=True):
            st.session_state.selected_incident = None
            st.rerun()

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