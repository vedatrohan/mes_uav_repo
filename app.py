import math
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
import regex as re
import plotly.graph_objects as go

def parse_prop_string(p_str):
    p_str = str(p_str).upper().strip()
    # 1. Check for explicit separators like '*' or 'X' (e.g., APC12*6, APC 10x4.5)
    if '*' in p_str or 'X' in p_str:
        parts = re.split(r'\*|X', p_str)
        dia_matches = re.findall(r'\d+\.?\d*', parts[0])
        pitch_matches = re.findall(r'\d+\.?\d*', parts[1])
        if dia_matches and pitch_matches:
            return float(dia_matches[-1]), float(pitch_matches[0])
    else:
        # 2. Check for 4-digit codes without separators (e.g., 9047, 8043, 1047)
        nums = re.findall(r'\d+', p_str)
        if nums:
            num_str = nums[-1]
            if len(num_str) == 4:
                dia = float(num_str[:2])
                # E.g., 8043 -> dia=80. Real diameter is 8.0
                if dia >= 20: 
                    dia /= 10.0
                pitch = float(num_str[2:]) / 10.0
                return dia, pitch
    return None, None

def safe_get_coef(row, key, default=0.0):
    """Safely extracts coefficient, avoiding NaN or missing column errors."""
    if key in row and pd.notna(row[key]):
        try:
            return float(row[key])
        except:
            return default
    return default

st.set_page_config(page_title="Bilkent UAV Sizing Suite", layout="wide")

# -------------------------------------------------------------
# GOOGLE DRIVE / SHEETS MASS BUILDUP INGESTION
# -------------------------------------------------------------
st.sidebar.header("Mass Buildup (Google Drive)")

design_sheet_id = "1ksGFylLwYRefZ5e4smVkHqotms8hJYrnHfDF-Msib30"
design_sheet_gid = "2084851165"

database_sheet_id = "1XaC-NVIDd16O-uIKxHvFW03QlGsIyrw7jBEeMRYMFBY"
motor_sheet_gid = "1771767346"
propeller_sheet_gid = "31584601"

@st.cache_data
def load_gsheet_csv(sheet_id, gid, header_row=0):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    try:
        return pd.read_csv(url, header=header_row, decimal=',')
    except Exception as e:
        st.error(f"Failed to load database. Ensure the sheet is public. Error: {e}")
        return pd.DataFrame()

df_motors = load_gsheet_csv(database_sheet_id, motor_sheet_gid, header_row=2)
df_props = load_gsheet_csv(database_sheet_id, propeller_sheet_gid, header_row=1)


@st.cache_data(ttl=60)
def load_and_clean_mass_table(sheet_id: str, gid: str):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    raw_df = pd.read_csv(url, header=None)
    
    header_idx = None
    for idx, row in raw_df.iterrows():
        row_vals = [str(x).strip().lower() for x in row.dropna().tolist()]
        if any("ürün" in v or "urun" in v or "kategori" in v for v in row_vals):
            header_idx = idx
            break
            
    if header_idx is None:
        raise ValueError("Tablo başlık satırı bulunamadı.")

    df = raw_df.iloc[header_idx + 1:].copy()
    df.columns = [str(c).strip() for c in raw_df.iloc[header_idx]]
    df = df.dropna(how="all", axis=1).dropna(how="all", axis=0)
    
    def normalize_str(s):
        return (
            str(s).strip().lower()
            .replace("ı", "i").replace("ğ", "g").replace("ü", "u")
            .replace("ş", "s").replace("ö", "o")
            .replace("ç", "c").replace(" ", "")
        )

    urun_col, weight_col, x_col, y_col, z_col = None, None, None, None, None

    for col in df.columns:
        norm = normalize_str(col)
        if any(k in norm for k in ["urun", "item", "part"]):
            urun_col = col
        elif any(k in norm for k in ["agirlik", "mass", "weight"]):
            weight_col = col
        elif norm.startswith("x"):
            x_col = col
        elif norm.startswith("y"):
            y_col = col
        elif norm.startswith("z"):
            z_col = col

    if not weight_col or not urun_col:
        raise KeyError(f"Zorunlu sütunlar bulunamadı. Mevcut sütunlar: {list(df.columns)}")

    df = df[df[urun_col].notna()]
    df = df[~df[urun_col].astype(str).str.contains("Toplam|Total", case=False)]

    for col in [weight_col, x_col, y_col, z_col]:
        if col and col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .str.extract(r"([-+]?\d*\.?\d+)", expand=False)
                .astype(float)
            )

    rename_map = {urun_col: "Ürün", weight_col: "Ağırlık(gr)"}
    if x_col: rename_map[x_col] = "X (mm)"
    if y_col: rename_map[y_col] = "Y (mm)"
    if z_col: rename_map[z_col] = "Z (mm)"
    df = df.rename(columns=rename_map)

    return df

live_mtow_kg = 3.0
x_cg_mm = 400.0
use_live_sheet = False

try:
    df_mass = load_and_clean_mass_table(design_sheet_id, design_sheet_gid)
    total_mass_gr = df_mass["Ağırlık(gr)"].sum()
    live_mtow_kg = total_mass_gr / 1000.0
    
    total_moment_x = (df_mass["Ağırlık(gr)"] * df_mass["X (mm)"]).sum()
    x_cg_mm = total_moment_x / total_mass_gr
    
    st.sidebar.success("Mass table synced from Drive.")
    st.sidebar.metric("Live MTOW", f"{live_mtow_kg:.3f} kg")
    st.sidebar.metric("Longitudinal CG (from Nose)", f"{x_cg_mm:.1f} mm")
    use_live_sheet = True
except Exception as e:
    st.sidebar.warning(f"Could not load live sheet: {e}")
    st.sidebar.info("Falling back to manual weights.")

# -------------------------------------------------------------
# CONFIGURATION & STATE
# -------------------------------------------------------------
default_config = {
    "target_mtow": float(live_mtow_kg),
    "v_to": 11.0,
    "s_g": 20.0,
    "runway_friction_idx": 1,
    "v_cruise": 16.0,
    "v_climb": 13.0,
    "climb_rate": 3.0,
    "n_turn": 1.41,
    "v_stall": 10.0,
    "cl_max_est": 1.4,
    "rho": 1.225,
    "mu_air": 1.789e-5,
    "g": 9.80665,
    "cd0_active": 0.035,
    "x_le_wing": 350.0,
    "eta_cruise": 0.72,
    "eta_climb": 0.65,
    "eta_to": 0.50,
    "eta_motor_esc": 0.82,
    "sm_range": (8.0, 15.0),
    "prop_dia_in": 12.0,
    "prop_pitch_in": 6.0,
    "prop_rpm": 7500.0,
    "prop_eta_max": 0.78,
    "use_dynamic_prop": True,
    "gust_velocity": 7.0,
    "n_pos_limit": 3.8
}

for key, val in default_config.items():
    if key not in st.session_state:
        st.session_state[key] = val

if use_live_sheet:
    st.session_state["target_mtow"] = float(live_mtow_kg)

st.title("MES İHA Konsept Tasarım Aracı")

with st.sidebar:
    st.header("Statik Stabilite Marj Sınırları")
    sm_range = st.slider(
        "Allowable Static Margin Range (% MAC)",
        min_value=-5.0,
        max_value=35.0,
        value=st.session_state.sm_range,
        step=0.5,
        key="sm_range"
    )
    sm_min, sm_max = sm_range

    st.markdown("---")
    st.header("Powertrain & Propeller Setup")
    use_dynamic_prop = st.checkbox("Model Propeller Efficiency via Advance Ratio (J)?", key="use_dynamic_prop")
    
    if use_dynamic_prop:
        prop_dia_in = st.number_input("Propeller Diameter (inches)", value=12.0, step=0.5, key="prop_dia_in")
        prop_pitch_in = st.number_input("Propeller Pitch (inches)", value=6.0, step=0.5, key="prop_pitch_in")
        prop_rpm = st.number_input("Operating Engine / Prop RPM", value=7500.0, step=250.0, key="prop_rpm")
        prop_eta_max = st.slider("Max Propeller Efficiency (eta_max)", 0.60, 0.88, 0.78, step=0.01, key="prop_eta_max")

        d_m = prop_dia_in * 0.0254
        p_m = prop_pitch_in * 0.0254
        n_rps = prop_rpm / 60.0
        j_pitch = p_m / d_m

        def calc_eta_prop(v_mps):
            if n_rps <= 0 or d_m <= 0:
                return 0.5
            j = v_mps / (n_rps * d_m)
            j_opt = 0.75 * j_pitch
            if j <= 0:
                return 0.15
            if j >= j_pitch:
                return 0.05
            eta = prop_eta_max * (1.0 - ((j - j_opt) / j_opt)**2)
            return max(float(eta), 0.15)

        eta_cruise = calc_eta_prop(st.session_state.v_cruise)
        eta_climb = calc_eta_prop(st.session_state.v_climb)
        eta_to = calc_eta_prop(st.session_state.v_to / math.sqrt(2))

        st.caption(f"Calculated Advance Ratios & Efficiencies:")
        st.write(f"- Cruise J: `{st.session_state.v_cruise / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_cruise:.3f}`")
        st.write(f"- Climb J: `{st.session_state.v_climb / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_climb:.3f}`")
        st.write(f"- Takeoff J: `{(st.session_state.v_to / math.sqrt(2)) / (n_rps * d_m):.2f}` → $\\eta_p$: `{eta_to:.3f}`")
    else:
        eta_cruise = st.slider("Cruise Propeller Efficiency", 0.50, 0.85, step=0.01, key="eta_cruise")
        eta_climb = st.slider("Climb Propeller Efficiency", 0.45, 0.80, step=0.01, key="eta_climb")
        eta_to = st.slider("Takeoff Avg Propeller Efficiency", 0.35, 0.65, step=0.01, key="eta_to")

    eta_motor_esc = st.slider("Motor & ESC Efficiency", 0.70, 0.95, step=0.01, key="eta_motor_esc")

def update_cd0_callback():
    st.session_state.cd0_active = st.session_state.temp_recalc_cd0

# -------------------------------------------------------------
# STAGE 1: MISSION PROFILE
# -------------------------------------------------------------
st.header("1. Görev Profili ve Parametreler")
col_p1, col_p2, col_p3 = st.columns(3)

with col_p1:
    st.subheader("Takeoff & Surface")
    target_mtow = st.number_input("Target MTOW (kg)", step=0.1, key="target_mtow")
    v_to = st.number_input("Takeoff Speed V_TO (m/s)", step=0.5, key="v_to")
    s_g = st.number_input("Ground Run Limit S_g (m)", step=1.0, key="s_g")
    runway_friction = st.number_input(
    "Runway Friction Coefficient", 
    min_value=0.0, 
    max_value=1.0, 
    value=0.08, 
    step=0.01, 
    key="runway_friction"
)

def update_from_bank():
    phi_rad = np.radians(st.session_state.bank_angle)
    # Clamp cos to avoid division by zero near 90 deg
    cos_phi = max(np.cos(phi_rad), 1e-4)
    st.session_state.n_turn = float(round(1.0 / cos_phi, 2))

def update_from_load():
    n = max(st.session_state.n_turn, 1.0)
    # phi = arccos(1 / n)
    phi_deg = np.degrees(np.arccos(1.0 / n))
    st.session_state.bank_angle = float(round(phi_deg, 1))


with col_p2:
    st.subheader("Speeds & Maneuver")
    v_cruise = st.number_input("Cruise Speed (m/s)", step=0.5, key="v_cruise")
    v_climb = st.number_input("Climb Speed (m/s)", step=0.5, key="v_climb")
    climb_rate = st.number_input("Rate of Climb V_v (m/s)", step=0.5, key="climb_rate")
    climb_angle = np.degrees(np.arcsin(climb_rate / v_climb))
    st.caption(f"Tırmanma Açısı (γ): **{climb_angle:.1f}°**")

    bank_angle = st.slider(
        "Bank Angle φ (°)",
        min_value=0.0,
        max_value=80.0,
        step=1.0,
        key="bank_angle",
        on_change=update_from_bank
    )

    n_turn = st.slider(
        "Sustained Turn Load Factor n",
        min_value=1.0,
        max_value=5.75,
        step=0.05,
        key="n_turn",
        on_change=update_from_load
    )

with col_p3:
    st.subheader("Atmosphere & Aero Limits")
    v_stall = st.number_input("Stall Speed V_s (m/s)", step=0.5, key="v_stall")
    cl_max_est = st.number_input("Estimated CL_max", step=0.05, key="cl_max_est")
    rho = st.number_input("Density rho (kg/m^3)", format="%.3f", key="rho")
    mu_air = st.number_input("Dynamic Viscosity mu (kg/(m*s))", format="%.3e", key="mu_air")
    g = st.number_input("Gravity g (m/s^2)", format="%.5f", key="g")
    cd0_active = st.number_input("Parasite Drag CD0", format="%.4f", key="cd0_active")
    cd_to = st.number_input("Takeoff CD (CD_to)", value=0.050, step=0.005, format="%.3f", key="cd_to")
    cl_to = st.number_input("Takeoff CL (CL_to)", value=0.60, step=0.05, key="cl_to")

# -------------------------------------------------------------
# STAGE 2: P/W CONSTRAINT ANALYSIS
# -------------------------------------------------------------
st.header("2. Master Constraint Analysis (Power-to-Weight)")

col_ar, col_plot = st.columns([1, 2])

with col_ar:
    ar = st.slider("Aspect Ratio (AR)", 4.0, 12.0, 7.0, step=0.5)
    e0 = 1.14 - 0.0801 * (ar**0.68)
    k_factor = 1.0 / (math.pi * ar * e0)
    
    max_ws_plot = st.number_input("Plot Max W/S (kg/m^2)", value=16.0, step=1.0)
    max_pw_plot = st.number_input("Plot Max P/W (W/kg)", value=350.0, step=25.0)

    q_cruise = 0.5 * rho * (v_cruise**2)
    q_climb = 0.5 * rho * (v_climb**2)
    v_to_avg = v_to / math.sqrt(2)
    q_to_avg = 0.5 * rho * (v_to_avg**2)
    q_stall = 0.5 * rho * (v_stall**2)

    ws_crit_pa = q_stall * cl_max_est
    opt_ws_kgm2 = ws_crit_pa / g

    tw_cruise = (q_cruise * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_cruise)
    tw_climb = (climb_rate / v_climb) + (q_climb * cd0_active / ws_crit_pa) + (k_factor * ws_crit_pa / q_climb)
    tw_turn = q_cruise * ((cd0_active / ws_crit_pa) + ws_crit_pa * k_factor * ((n_turn / q_cruise)**2))
    tw_to = ((v_to**2) / (2 * g * s_g)) + (q_to_avg * cd_to / ws_crit_pa) + runway_friction * (1.0 - (q_to_avg * cl_to / ws_crit_pa))

    pw_crit = {
        "Cruise": (tw_cruise * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Climb": (tw_climb * g * v_climb) / (eta_climb * eta_motor_esc),
        "Turn": (tw_turn * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Takeoff Ground Run": (tw_to * g * v_to_avg) / (eta_to * eta_motor_esc)
    }

    governing_phase = max(pw_crit, key=pw_crit.get)
    opt_pw = pw_crit[governing_phase]
    s_req = target_mtow / opt_ws_kgm2
    total_power_req_w = target_mtow * opt_pw

    st.metric("Optimal Wing Loading (W/S)", f"{opt_ws_kgm2:.2f} kg/m^2")
    st.metric("Governing Sizing Phase", governing_phase)
    st.metric("Minimum Electrical P/W", f"{opt_pw:.1f} W/kg")
    st.metric("Required Electrical Power", f"{total_power_req_w:.1f} W")
    st.metric("Required Wing Area (S)", f"{s_req:.3f} m^2")

with col_plot:
    ws_pa_range = np.linspace(1.0, max_ws_plot * g, 300)
    ws_kgm2_range = ws_pa_range / g

    tw_c = (q_cruise * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_cruise)
    tw_cl = (climb_rate / v_climb) + (q_climb * cd0_active / ws_pa_range) + (k_factor * ws_pa_range / q_climb)
    tw_t = q_cruise * ((cd0_active / ws_pa_range) + ws_pa_range * k_factor * ((n_turn / q_cruise)**2))
    tw_to_curve = ((v_to**2) / (2 * g * s_g)) + (q_to_avg * cd_to / ws_pa_range) + runway_friction * (1.0 - (q_to_avg * cl_to / ws_pa_range))

    pw_c = (tw_c * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_cl = (tw_cl * g * v_climb) / (eta_climb * eta_motor_esc)
    pw_t = (tw_t * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_to_curve = (tw_to_curve * g * v_to_avg) / (eta_to * eta_motor_esc)

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=100)
    ax.plot(ws_kgm2_range, pw_c, label="Cruise", color="crimson")
    ax.plot(ws_kgm2_range, pw_cl, label="Climb", color="royalblue")
    ax.plot(ws_kgm2_range, pw_t, label="Turn", color="purple")
    ax.plot(ws_kgm2_range, pw_to_curve, label="Takeoff", color="forestgreen")
    ax.axvline(x=opt_ws_kgm2, label="Stall Limit", color="black", linestyle="--", lw=1.5)
    ax.plot(opt_ws_kgm2, opt_pw, "ro", markersize=8, label=f"Design Point ({opt_ws_kgm2:.2f}, {opt_pw:.1f})")

    upper_pw = np.maximum.reduce([pw_c, pw_cl, pw_t, pw_to_curve])
    feasible_mask = ws_kgm2_range <= opt_ws_kgm2
    ax.fill_between(ws_kgm2_range[feasible_mask], upper_pw[feasible_mask], max_pw_plot, color="lightgray", alpha=0.4)

    ax.set_xlim(0, max_ws_plot)
    ax.set_ylim(0, max_pw_plot)
    ax.set_xlabel("Wing Loading W/S (kg/m^2)")
    ax.set_ylabel("Electrical Power Loading P/W (W/kg)")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8.0)
    st.pyplot(fig)

# ---------------------------------------------------------
# 3. ELECTRONIC COMPONENTS
# ---------------------------------------------------------
st.header("3. Electronic Components")
st.subheader("Battery Configuration")
col_bat1, col_bat2 = st.columns(2)
with col_bat1:
    batt_s = st.number_input("Battery Voltage (Cells / S)", min_value=1, max_value=14, value=4, step=1)
with col_bat2:
    batt_cap = st.number_input("Battery Capacity (mAh)", min_value=100, max_value=50000, value=5000, step=100)

st.markdown("---")

col_el1, col_el2 = st.columns(2)
selected_motor_compatible_props = []

# --- MOTOR COLUMN ---
with col_el1:
    st.subheader("Motor Selection")
    motor_type = st.radio("Motor Definition", ["Database", "Custom"], horizontal=True)

    active_kv = 1000
    active_max_amp = 30.0
    active_max_power = 400.0
    active_ir_mohm = 50.0

    if motor_type == "Database":
        if not df_motors.empty:
            df_motors["Display"] = df_motors["Üretici"].astype(str) + " " + \
                                   df_motors["Model"].astype(str) + " - " + \
                                   df_motors["KV"].astype(str) + "KV"
            
            motor_choice = st.selectbox("Select a Motor", df_motors["Display"].dropna().unique())
            motor_data = df_motors[df_motors["Display"] == motor_choice].iloc[0]
            
            active_kv = float(motor_data.get('KV', 1000))
            active_max_amp = float(motor_data.get('Max. Amper', 30))
            active_max_power = float(motor_data.get('Max. Güç', 400))
            active_ir_mohm = float(motor_data.get('İç Direnç (mOhm)', 50))
            
            st.info(f"**Weight:** {motor_data.get('Ağırlık', 'N/A')} g  \n"
                    f"**Max Power:** {active_max_power} W  \n"
                    f"**Max Amps:** {active_max_amp} A  \n"
                    f"**Internal Res:** {active_ir_mohm} mΩ")
            
            prop_cols = motor_data.index[10:] 
            valid_props = motor_data[prop_cols].dropna().astype(str).tolist()
            selected_motor_compatible_props = [p.strip() for p in valid_props if p.strip() and p.lower() != "nan"]
        else:
            st.warning("Motor database is empty or could not be loaded.")
    else:
        active_kv = st.number_input("KV Rating", min_value=10, value=1000, step=50)
        c_motor_weight = st.number_input("Motor Weight (g)", min_value=1.0, value=100.0, step=5.0)
        active_max_amp = st.number_input("Max Continuous Current (A)", min_value=1.0, value=30.0, step=1.0)
        active_max_power = st.number_input("Max Power (W)", min_value=1.0, value=400.0, step=10.0)
        active_ir_mohm = st.number_input("Internal Resistance (mOhm)", min_value=1.0, value=50.0, step=1.0)

    # --- RPM Math & Current Slider ---
    st.markdown("**Motor RPM Performance**")
    v_batt_nominal = batt_s * 3.7
    rpm_empty = active_kv * v_batt_nominal
    
    st.metric("Empty RPM (Nominal Voltage)", f"{rpm_empty:,.0f} RPM", f"{v_batt_nominal:.1f} V")
    
    sim_current = st.slider("Simulate Current Draw (A)", 0.0, float(active_max_amp), value=float(active_max_amp)/2)
    v_drop = sim_current * (active_ir_mohm / 1000.0)
    rpm_loaded = active_kv * (v_batt_nominal - v_drop)
    
    st.metric(f"Loaded RPM @ {sim_current}A", f"{max(0, rpm_loaded):,.0f} RPM", f"Voltage Drop: -{v_drop:.2f} V", delta_color="inverse")


# --- PROPELLER COLUMN ---
with col_el2:
    st.subheader("Propeller Selection")
    prop_type = st.radio("Propeller Definition", ["Database", "Custom"], horizontal=True)

    active_prop_dia_inch = 10.0
    ct_coeffs = [0.0, 0.0, 0.0, 0.100]  # a(J^3), b(J^2), c(J), d(const)
    cp_coeffs = [0.0, 0.0, 0.0, 0.050]

    if prop_type == "Database":
        if not df_props.empty:
            df_props["Display"] = df_props["Üretici"].astype(str) + " " + \
                                  df_props["Diameter"].astype(str) + "x" + \
                                  df_props["Pitch"].astype(str)
            
            if motor_type == "Database" and selected_motor_compatible_props:
                st.caption("Filtered to compatible propellers for selected motor:")
                prop_options = selected_motor_compatible_props
            else:
                prop_options = df_props["Display"].dropna().unique()
            
            prop_choice = st.selectbox("Select a Propeller", prop_options)

            # SMART MATCHING: Extract numbers and find the matching row
            target_dia, target_pitch = parse_prop_string(prop_choice)
            prop_data = None
            
            if target_dia is not None and target_pitch is not None:
                for idx, row in df_props.iterrows():
                    try:
                        db_dia = float(row['Diameter'])
                        db_pitch = float(row['Pitch'])
                        if abs(db_dia - target_dia) < 0.05 and abs(db_pitch - target_pitch) < 0.05:
                            prop_data = row
                            break  
                    except:
                        continue

            if prop_data is not None:
                active_prop_dia_inch = float(prop_data.get('Diameter', 10.0))
                st.success(f"Matched Profile: **{prop_data.get('Üretici', '')} {active_prop_dia_inch}x{prop_data.get('Pitch', '')}**")
                
                # Extract and SCALE DOWN by 1000
                ct_coeffs = [
                    safe_get_coef(prop_data, 'CT vs J (a)'),
                    safe_get_coef(prop_data, 'CT vs J (b)'),
                    safe_get_coef(prop_data, 'CT vs J (c)'),
                    safe_get_coef(prop_data, 'CT vs J (d)')
                ]
                cp_coeffs = [
                    safe_get_coef(prop_data, 'CP vs J (a)'),
                    safe_get_coef(prop_data, 'CP vs J (b)'),
                    safe_get_coef(prop_data, 'CP vs J (c)'),
                    safe_get_coef(prop_data, 'CP vs J (d)')
                ]
            else:
                st.warning(f"Extracted [Dia: {target_dia}\", Pitch: {target_pitch}\"] from '{prop_choice}', but no exact match exists in the Propeller Database. Defaulting to Custom.")
        else:
            st.warning("Propeller database is empty or could not be loaded.")

    else:
        active_prop_dia_inch = st.number_input("Diameter (inch)", min_value=1.0, value=10.0, step=0.5)
        c_prop_pitch = st.number_input("Pitch (inch)", min_value=1.0, value=4.5, step=0.5)
        
        st.markdown("**Thrust Coefficient ($C_T$) Curve Fits**")
        st.caption("Format: $C_T = a J^3 + b J^2 + c J + d$")
        ct1, ct2, ct3, ct4 = st.columns(4)
        ct_coeffs[0] = ct1.number_input("$C_T$ (a - cubic)", value=0.0000, format="%.4f")
        ct_coeffs[1] = ct2.number_input("$C_T$ (b - square)", value=0.0000, format="%.4f")
        ct_coeffs[2] = ct3.number_input("$C_T$ (c - linear)", value=-0.0500, format="%.4f")
        ct_coeffs[3] = ct4.number_input("$C_T$ (d - const)", value=0.1000, format="%.4f")
        
        st.markdown("**Power Coefficient ($C_P$) Curve Fits**")
        st.caption("Format: $C_P = a J^3 + b J^2 + c J + d$")
        cp1, cp2, cp3, cp4 = st.columns(4)
        cp_coeffs[0] = cp1.number_input("$C_P$ (a - cubic)", value=0.0000, format="%.4f")
        cp_coeffs[1] = cp2.number_input("$C_P$ (b - square)", value=0.0000, format="%.4f")
        cp_coeffs[2] = cp3.number_input("$C_P$ (c - linear)", value=-0.0200, format="%.4f")
        cp_coeffs[3] = cp4.number_input("$C_P$ (d - const)", value=0.0500, format="%.4f")


# ---------------------------------------------------------
# PERFORMANCE, FLIGHT STAGES & EFFICIENCY CURVE
# ---------------------------------------------------------
st.markdown("---")
st.subheader("Propulsion & Flight Stage Analysis")
st.markdown("Flight speeds are automatically pulled from **Section 1: Görev Profili**.")

# Use session state speeds
v_to = st.session_state.get("v_to", 11.0)
v_climb = st.session_state.get("v_climb", 13.0)
v_cruise = st.session_state.get("v_cruise", 16.0)

fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    st.info(f"**Takeoff Speed:** {v_to} m/s")
    req_t_takeoff = st.number_input("Req. Thrust Takeoff (N)", value=15.0)
with fcol2:
    st.info(f"**Climb Speed:** {v_climb} m/s")
    req_t_climb = st.number_input("Req. Thrust Climb (N)", value=12.0)
with fcol3:
    st.info(f"**Cruise Speed:** {v_cruise} m/s")
    req_t_cruise = st.number_input("Req. Thrust Cruise (N)", value=6.0)

# Corrected polynomial evaluator
def calc_poly(coeffs, j):
    # coeffs = [a, b, c, d] for a*J^3 + b*J^2 + c*J + d
    return coeffs[0]*(j**3) + coeffs[1]*(j**2) + coeffs[2]*j + coeffs[3]

rho = st.session_state.get("rho", 1.225)
D_m = active_prop_dia_inch * 0.0254

def find_operating_point(V, T_req, rpm_max, ct_cf, cp_cf):
    rpms = np.linspace(100, rpm_max, 1000)
    ns = rpms / 60.0
    
    Js = V / (ns * D_m)
    CTs = calc_poly(ct_cf, Js)
    CPs = calc_poly(cp_cf, Js)
    
    # Filter out invalid physical regimes (negative thrust or power)
    valid = (CTs > 0) & (CPs > 0)
    if not np.any(valid): return None
    
    ns, CTs, CPs, Js = ns[valid], CTs[valid], CPs[valid], Js[valid]
    
    T_avail = rho * (ns**2) * (D_m**4) * CTs
    
    if np.max(T_avail) < T_req:
        return None  # Max RPM reached, can't produce required thrust
    
    idx = np.argmin(np.abs(T_avail - T_req))
    P_aero = rho * (ns[idx]**3) * (D_m**5) * CPs[idx]
    eta = Js[idx] * (CTs[idx] / CPs[idx]) if CPs[idx] > 0 else 0
    
    return {"RPM": ns[idx] * 60, "J": Js[idx], "Power": P_aero, "Efficiency": eta}

stages = {
    "Takeoff": find_operating_point(v_to, req_t_takeoff, rpm_empty, ct_coeffs, cp_coeffs),
    "Climb": find_operating_point(v_climb, req_t_climb, rpm_empty, ct_coeffs, cp_coeffs),
    "Cruise": find_operating_point(v_cruise, req_t_cruise, rpm_empty, ct_coeffs, cp_coeffs)
}

rcol1, rcol2, rcol3 = st.columns(3)
cols = [rcol1, rcol2, rcol3]

for i, (stage_name, data) in enumerate(stages.items()):
    with cols[i]:
        st.markdown(f"**{stage_name} Requirements**")
        if data is None:
            st.error(f"Cannot meet thrust. Max RPM ({rpm_empty:.0f}) reached.")
        else:
            p_color = "normal" if data["Power"] <= active_max_power else "off"
            if data["Power"] > active_max_power:
                st.error(f"**Power Exceeded Motor Max!**")
                
            st.metric("Req. RPM", f"{data['RPM']:,.0f} RPM")
            st.metric("Power Drawn", f"{data['Power']:.1f} W", f"Limit: {active_max_power} W", delta_color=p_color)
            st.metric("Prop Efficiency", f"{data['Efficiency']*100:.1f} %")

# --- Plot Efficiency Curve ---
j_plot = np.linspace(0.01, 1.2, 300)
ct_plot = calc_poly(ct_coeffs, j_plot)
cp_plot = calc_poly(cp_coeffs, j_plot)

valid_j = (ct_plot > 0) & (cp_plot > 0)
j_plot, ct_plot, cp_plot = j_plot[valid_j], ct_plot[valid_j], cp_plot[valid_j]

eta_plot = j_plot * (ct_plot / cp_plot)

fig = go.Figure()
fig.add_trace(go.Scatter(x=j_plot, y=eta_plot*100, mode='lines', name='Propeller Efficiency', line=dict(color='royalblue', width=3)))

colors = {"Takeoff": "red", "Climb": "orange", "Cruise": "green"}
for stage_name, data in stages.items():
    if data is not None:
        fig.add_trace(go.Scatter(
            x=[data["J"]], y=[data["Efficiency"]*100], 
            mode='markers+text', name=stage_name,
            marker=dict(size=12, color=colors[stage_name], symbol='cross'),
            text=[stage_name], textposition="top center"
        ))

fig.update_layout(
    title=f"Propeller Efficiency ($\eta$) vs Advance Ratio ($J$) - {active_prop_dia_inch}\" Prop",
    xaxis_title="Advance Ratio ($J = V/nD$)",
    yaxis_title="Efficiency (%)",
    yaxis_range=[0, 100], hovermode="x unified"
)

st.plotly_chart(fig, use_container_width=True)
# -------------------------------------------------------------
# STAGE 3: WING SIZING 
# -------------------------------------------------------------
st.header("4. Wing Sizing")
wingspan_total = math.sqrt(ar * s_req)

col_w1, col_w2 = st.columns(2)

with col_w1:
    apply_taper = st.checkbox("Apply Wing Taper (lambda < 1.0)?", value=False)
    if not apply_taper:
        lambda_val = 1.0
        c_root = s_req / wingspan_total
        c_tip = c_root
        mac = c_root
    else:
        lambda_val = st.slider("Taper Ratio lambda (c_tip / c_root)", 0.2, 1.0, 0.65, step=0.05)
        c_root = (2.0 / (1.0 + lambda_val)) * math.sqrt(s_req / ar)
        c_tip = lambda_val * c_root
        mac = (2.0 / 3.0) * c_root * ((1.0 + lambda_val + lambda_val**2) / (1.0 + lambda_val))

    x_le_wing = st.number_input("Wing Leading Edge Location from Nose X_LE (mm)", step=10.0, key="x_le_wing")
    x_ac_wing = x_le_wing + (0.25 * mac * 1000.0)
    static_margin = ((x_ac_wing - x_cg_mm) / (mac * 1000.0)) * 100.0
    
    st.markdown("---")
    re_tip_threshold = st.slider("Tip Reynolds Warning Threshold", min_value=50000, max_value=300000, value=100000, step=10000)

with col_w2:
    st.write("Calculated Metrics:")
    sm1, sm2 = st.columns(2)
    sm1.metric("Wingspan (b)", f"{wingspan_total:.2f} m")
    sm1.metric("Mean Aero Chord (MAC)", f"{mac * 100:.1f} cm")
    sm2.metric("Wing Aerodynamic Center", f"{x_ac_wing:.1f} mm")

    re_cruise = (rho * v_cruise * mac) / mu_air if mu_air > 0 else 0.0
    re_tip = (rho * v_cruise * c_tip) / mu_air if mu_air > 0 else 0.0
    
    st.caption(f"Cruise Reynolds Number (based on MAC): **Re = {re_cruise:,.0f}**")
    st.caption(f"Tip Reynolds Number: **Re_tip = {re_tip:,.0f}**")

    if re_tip < re_tip_threshold:
        st.warning(f"Flow Separation Risk: Tip Re ({re_tip:,.0f}) is below the {re_tip_threshold:,} threshold. Consider increasing tip chord or cruise speed.")

# -------------------------------------------------------------
# STAGE 5: AIRFOIL TRANSLATION
# -------------------------------------------------------------
st.header("5. Airfoil 2D Lift Requirements")
cl_3d_cruise = (2.0 * target_mtow * g) / (rho * (v_cruise**2) * s_req)
cl_2d_cruise = cl_3d_cruise / (1.0 - (cl_3d_cruise / (math.pi * ar * e0)))
cl_2d_stall = cl_max_est / (1.0 - (cl_max_est / (math.pi * ar * e0)))

af1, af2, af3 = st.columns(3)
af1.metric("Target 3D Cruise CL", f"{cl_3d_cruise:.3f}")
af2.metric("Target 2D Airfoil Cl (Cruise)", f"{cl_2d_cruise:.3f}")
af3.metric("Target 2D Airfoil Cl_max", f"{cl_2d_stall:.3f}")

# -------------------------------------------------------------
# STAGE 6: EMPENNAGE SIZING & TAIL GEOMETRY
# -------------------------------------------------------------
st.header("6. Empennage Sizing & Detailed Geometry")
tl1, tl2 = st.columns(2)

# ==========================================
# 1. HORIZONTAL TAIL (HT)
# ==========================================
with tl1:
    st.subheader("Horizontal Tail")
    l_h = st.slider(
        "Horizontal Moment Arm l_H (m)", 0.40, 1.20, 0.65, step=0.05,
        help="Distance from Wing AC to Horizontal Tail AC (at tail MAC quarter-chord)"
    )
    v_h = st.slider("Volume Coefficient V_H", 0.40, 0.85, 0.60, step=0.05)
    
    # Target surface area fixed by volume coefficient
    s_h = (v_h * s_req * mac) / l_h

    # Bi-directional Session State Initialization for HT
    if "ar_h" not in st.session_state:
        st.session_state.ar_h = 4.5
    if "b_h" not in st.session_state:
        st.session_state.b_h = round(math.sqrt(st.session_state.ar_h * s_h), 3)

    def sync_ht_from_ar():
        st.session_state.b_h = round(math.sqrt(st.session_state.ar_h * s_h), 3)

    def sync_ht_from_b():
        st.session_state.ar_h = round((st.session_state.b_h ** 2) / s_h, 2)

    col_ar_h, col_b_h = st.columns(2)
    with col_ar_h:
        ar_h = st.slider(
            "Aspect Ratio (AR_H)", 2.0, 8.0, step=0.1,
            key="ar_h", on_change=sync_ht_from_ar
        )
    with col_b_h:
        b_h = st.slider(
            "Span b_H (m)", 0.30, 2.00, step=0.01,
            key="b_h", on_change=sync_ht_from_b
        )

    taper_h = st.slider("Taper Ratio λ_H (c_tip / c_root)", 0.30, 1.00, 1.00, step=0.05)

    # Tapered Chord & MAC Calculations
    c_root_h = (2.0 * s_h) / (b_h * (1.0 + taper_h))
    c_tip_h = taper_h * c_root_h
    mac_h = (2.0 / 3.0) * c_root_h * ((1.0 + taper_h + taper_h**2) / (1.0 + taper_h))
    
    # Spanwise MAC position (from centerline to one tip)
    y_mac_h = (b_h / 6.0) * ((1.0 + 2.0 * taper_h) / (1.0 + taper_h))

    ht_m1, ht_m2, ht_m3, ht_m4 = st.columns(4)
    ht_m1.metric("Area (S_H)", f"{s_h:.3f} m²")
    ht_m2.metric("Span (b_H)", f"{b_h * 100:.1f} cm")
    ht_m3.metric("Root Chord", f"{c_root_h * 100:.1f} cm")
    ht_m4.metric("MAC (c_H)", f"{mac_h * 100:.1f} cm")


# ==========================================
# 2. VERTICAL TAIL (VT)
# ==========================================
with tl2:
    st.subheader("Vertical Tail")
    l_v = st.slider(
        "Vertical Moment Arm l_V (m)", 0.40, 1.20, 0.65, step=0.05,
        help="Distance from Wing AC to Vertical Tail AC"
    )
    v_v = st.slider("Volume Coefficient V_V", 0.02, 0.06, 0.04, step=0.005)
    
    s_v = (v_v * s_req * wingspan_total) / l_v


    if "ar_v" not in st.session_state:
        st.session_state.ar_v = 1.8
    if "b_v" not in st.session_state:
        st.session_state.b_v = round(math.sqrt(st.session_state.ar_v * s_v), 3)

    def sync_vt_from_ar():
        st.session_state.b_v = round(math.sqrt(st.session_state.ar_v * s_v), 3)

    def sync_vt_from_b():
        st.session_state.ar_v = round((st.session_state.b_v ** 2) / s_v, 2)

    col_ar_v, col_b_v = st.columns(2)
    with col_ar_v:
        ar_v = st.slider(
            "Fin Aspect Ratio (AR_V)", 0.8, 4.0, step=0.1,
            key="ar_v", on_change=sync_vt_from_ar
        )
    with col_b_v:
        b_v = st.slider(
            "Height / Span b_V (m)", 0.15, 1.20, step=0.01,
            key="b_v", on_change=sync_vt_from_b
        )

    taper_v = st.slider("Taper Ratio λ_V (c_tip / c_root)", 0.30, 1.00, 1.00, step=0.05)


    c_root_v = (2.0 * s_v) / (b_v * (1.0 + taper_v))
    c_tip_v = taper_v * c_root_v
    mac_v = (2.0 / 3.0) * c_root_v * ((1.0 + taper_v + taper_v**2) / (1.0 + taper_v))
    

    z_mac_v = (b_v / 3.0) * ((1.0 + 2.0 * taper_v) / (1.0 + taper_v))

    vt_m1, vt_m2, vt_m3, vt_m4 = st.columns(4)
    vt_m1.metric("Area (S_V)", f"{s_v:.3f} m²")
    vt_m2.metric("Height (b_V)", f"{b_v * 100:.1f} cm")
    vt_m3.metric("Root Chord", f"{c_root_v * 100:.1f} cm")
    vt_m4.metric("MAC (c_V)", f"{mac_v * 100:.1f} cm")

st.markdown("---")
st.subheader("Longitudinal Static Stability Analysis")

tail_effectiveness = st.slider(
    "Tail Effectiveness Factor η_eff = (q_t/q) * (a_t/a_w) * (1 - dε/dα)", 
    0.20, 0.70, 0.45, step=0.01, 
    help="Represents dynamic pressure ratio, lift curve slope ratio, and downwash factor."
)

mac_mm = mac * 1000.0


delta_x_np_mm = (l_h * 1000.0) * (s_h / s_req) * tail_effectiveness

x_np = x_ac_wing + delta_x_np_mm
true_static_margin = ((x_np - x_cg_mm) / mac_mm) * 100.0

np_col1, np_col2, np_col3 = st.columns(3)
np_col1.metric("Wing Aerodynamic Center (X_AC)", f"{x_ac_wing:.1f} mm")
np_col2.metric("Aircraft Neutral Point (X_NP)", f"{x_np:.1f} mm", delta=f"+{delta_x_np_mm:.1f} mm aft shift")
np_col3.metric("Static Margin (SM)", f"{true_static_margin:.1f} % MAC")

if true_static_margin < sm_min:
    st.error(
        f"Statik marjin belirlenen sınırın (%{sm_min:.1f}) altında. Ağırlık merkezi öne kaydırılmalı ya da kuyruk hacmi/kolu arttırılmalı."
    )
elif true_static_margin > sm_max:
    st.warning(
        f"Statik marjin belirlenen sınırdan (%{sm_max:.1f}) yüksek. Yüksek trim sürtünmesi yaşanabilir, ağırlık merkezi geriye alınabilir ya da kuyruk etkisi azaltılabilir."
    )
else:
    st.success("Statik stabilite belirlenen sınırlar içerisinde.")
# -------------------------------------------------------------
# STAGE 7: DRAG BUILDUP VERIFICATION & BREAKDOWN
# -------------------------------------------------------------
st.header("7. Drag Buildup Verification")
dg1, dg2 = st.columns(2)

with dg1:
    st.subheader("Geometry & Friction Inputs")
    fuse_len = st.number_input("Fuselage Length (m)", value=0.90, step=0.05)
    fuse_dia = st.number_input("Fuselage Equivalent Diameter (m)", value=0.12, step=0.01)
    cf_fuse = st.number_input("Skin Friction Cf", value=0.005, format="%.4f")
    cd_wing_emp = st.number_input("Wing & Tail Profile Drag", value=0.012, format="%.4f")
    
    # Landing Gear Drag Buildup based on resim.png table
    st.markdown("**Landing Gear Frontal Areas (m²)**")
    
    # Dictionary mapping components to their D/q per frontal area
    gear_components = {
        "Regular wheel and tire": 0.25, 
        "Second wheel and tire in tandem": 0.15,
        "Streamlined wheel and tire": 0.18, 
        "Wheel and tire with fairing": 0.13, 
        "Streamline strut (1/6 < t/c < 1/3)": 0.05, 
        "Round strut or wire": 0.30, 
        "Flat spring gear leg": 1.40, 
        "Fork, bogey, irregular fitting": 1.20 
    }
    
    with st.expander("Configure Landing Gear Components", expanded=False):
        total_gear_dq = 0.0
        for name, dq_val in gear_components.items():
            area = st.number_input(f"{name} (D/q/A = {dq_val})", value=0.0, step=0.001, format="%.4f")
            total_gear_dq += area * dq_val
            
    # CD_gear = (D/q) / S
    gear_cd = total_gear_dq / s_req if s_req > 0 else 0.0

    # Aerodynamic Form Factor and Drag Computations
    s_wet_fuse = math.pi * fuse_dia * fuse_len * 0.8
    fineness = fuse_len / fuse_dia
    form_factor = 1.0 + (60.0 / (fineness**3)) + (fineness / 400.0)
    cd0_fuse = (cf_fuse * form_factor * s_wet_fuse) / s_req
    total_recalc_cd0 = cd0_fuse + cd_wing_emp + gear_cd

with dg2:
    st.subheader("Component & Breakdown Factors")
    f_col1, f_col2 = st.columns(2)
    f_col1.metric("Fineness Ratio (L/D)", f"{fineness:.2f}")
    f_col1.metric("Form Factor (FF)", f"{form_factor:.3f}")
    f_col2.metric("Fuselage S_wet", f"{s_wet_fuse:.3f} m²")
    f_col2.metric("Fuselage CD0", f"{cd0_fuse:.4f}")

    st.markdown("---")
    st.metric("Landing Gear CD", f"{gear_cd:.4f}")
    st.metric("Total Recalculated CD0", f"{total_recalc_cd0:.4f}")

    pct_fuse = (cd0_fuse / total_recalc_cd0) * 100.0 if total_recalc_cd0 > 0 else 0
    pct_wing_emp = (cd_wing_emp / total_recalc_cd0) * 100.0 if total_recalc_cd0 > 0 else 0
    pct_gear = (gear_cd / total_recalc_cd0) * 100.0 if total_recalc_cd0 > 0 else 0
    
    st.caption(f"Drag Share: Fuselage: **{pct_fuse:.1f}%** | Lifting Surfaces: **{pct_wing_emp:.1f}%** | Gear: **{pct_gear:.1f}%**")

    if abs(total_recalc_cd0 - st.session_state.cd0_active) > 0.003:
        st.warning(f"Drag mismatch: Initial {st.session_state.cd0_active:.4f} vs Buildup {total_recalc_cd0:.4f}")
    else:
        st.success("Drag assumption aligns with geometry.")

st.session_state.temp_recalc_cd0 = float(total_recalc_cd0)
st.button("Update Stage 1 Drag Assumption and Recalculate", on_click=update_cd0_callback)
