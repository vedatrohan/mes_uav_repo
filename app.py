import math
import re
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def parse_prop_string(p_str):
    p_str = str(p_str).upper().strip()
    # 1. Check for explicit separators like '*' or 'X' (e.g., APC12*6, APC 10x4.5)
    if "*" in p_str or "X" in p_str:
        parts = re.split(r"\*|X", p_str)
        dia_matches = re.findall(r"\d+\.?\d*", parts[0])
        pitch_matches = re.findall(r"\d+\.?\d*", parts[1])
        if dia_matches and pitch_matches:
            return float(dia_matches[-1]), float(pitch_matches[0])
    else:
        # 2. Check for 4-digit codes without separators (e.g., 9047, 8043, 1047)
        nums = re.findall(r"\d+", p_str)
        if nums:
            num_str = nums[-1]
            if len(num_str) == 4:
                dia = float(num_str[:2])
                if dia >= 20:
                    dia /= 10.0
                pitch = float(num_str[2:]) / 10.0
                return dia, pitch
    return None, None


def safe_get_coef(row, key, default=0.0):
    """Safely extracts coefficient, avoiding NaN or missing column errors."""
    if key in row and pd.notna(row[key]):
        try:
            val_str = str(row[key]).replace(",", ".")
            return float(val_str)
        except Exception:
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
        return pd.read_csv(url, header=header_row, decimal=",")
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

    df = raw_df.iloc[header_idx + 1 :].copy()
    df.columns = [str(c).strip() for c in raw_df.iloc[header_idx]]
    df = df.dropna(how="all", axis=1).dropna(how="all", axis=0)

    def normalize_str(s):
        return (
            str(s)
            .strip()
            .lower()
            .replace("ı", "i")
            .replace("ğ", "g")
            .replace("ü", "u")
            .replace("ş", "s")
            .replace("ö", "o")
            .replace("ç", "c")
            .replace(" ", "")
        )

    urun_col, kategori_col, weight_col = None, None, None
    x_col, y_col, z_col = None, None, None

    for col in df.columns:
        norm = normalize_str(col)
        if any(k in norm for k in ["urun", "item", "part"]):
            urun_col = col
        elif any(k in norm for k in ["kategori", "category"]):
            kategori_col = col
        elif any(k in norm for k in ["agirlik", "mass", "weight"]):
            weight_col = col
        elif norm.startswith("x"):
            x_col = col
        elif norm.startswith("y"):
            y_col = col
        elif norm.startswith("z"):
            z_col = col

    if not weight_col or not urun_col:
        raise KeyError(
            f"Zorunlu sütunlar bulunamadı. Mevcut sütunlar: {list(df.columns)}"
        )

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
    if kategori_col:
        rename_map[kategori_col] = "Kategori"
    if x_col:
        rename_map[x_col] = "X (mm)"
    if y_col:
        rename_map[y_col] = "Y (mm)"
    if z_col:
        rename_map[z_col] = "Z (mm)"
    df = df.rename(columns=rename_map)

    return df

# Fallbacks
live_mtow_kg = 3.0  # fallback manual weight in kg
payload_mass_kg = 0.5  # fallback payload mass in kg
x_cg_mm = 250.0  # fallback CG in mm
use_live_sheet = False

try:
  df_mass = load_and_clean_mass_table(design_sheet_id, design_sheet_gid)
  total_mass_gr = df_mass["Ağırlık(gr)"].sum()

  if total_mass_gr > 0:
    live_mtow_kg = total_mass_gr / 1000.0

    if "X (mm)" in df_mass.columns:
      total_moment_x = (df_mass["Ağırlık(gr)"] * df_mass["X (mm)"]).sum()
      x_cg_mm = total_moment_x / total_mass_gr

    # --- Extract Payload Mass ---
    if "Kategori" in df_mass.columns:
      # Normalize categories to be robust against turkish characters and casing
      norm_categories = (
          df_mass["Kategori"]
          .astype(str)
          .str.lower()
          .str.replace("ı", "i")
          .str.replace("ü", "u")
          .str.strip()
      )

      # Filter for 'faydali yuk' or 'payload'
      payload_mask = norm_categories.str.contains("faydali yuk|payload", regex=True)
      payload_mass_gr = df_mass.loc[payload_mask, "Ağırlık(gr)"].sum()
      payload_mass_kg = payload_mass_gr / 1000.0

    st.sidebar.success("Mass table synced from Drive.")
    st.sidebar.metric("Live MTOW", f"{live_mtow_kg:.3f} kg")
    st.sidebar.metric("Payload Mass", f"{payload_mass_kg:.3f} kg")
    st.sidebar.metric("Longitudinal CG (from Nose)", f"{x_cg_mm:.1f} mm")
    use_live_sheet = True
  else:
    st.sidebar.warning("Mass table is empty (sum = 0). Using manual defaults.")
except Exception as e:
  st.sidebar.warning(f"Could not load live sheet: {e}")
  st.sidebar.info("Falling back to manual weights.")

# -------------------------------------------------------------
# CONFIGURATION & STATE INITIALIZATION
# -------------------------------------------------------------
default_config = {
    "target_mtow": float(live_mtow_kg),
    "v_to": 11.0,
    "s_g": 20.0,
    "runway_friction": 0.08,
    "v_cruise": 16.0,
    "v_climb": 13.0,
    "climb_rate": 3.0,
    "v_descent": 3.0,
    "bank_angle": 45.0,
    "n_turn": 1.41,
    "v_stall": 10.0,
    "cl_max_est": 1.4,
    "rho": 1.225,
    "mu_air": 1.789e-5,
    "g": 9.80665,
    "cd0_active": 0.035,
    "cd_to": 0.050,
    "cl_to": 0.60,
    "x_le_wing": 350.0,
    "eta_cruise": 0.72,
    "eta_climb": 0.65,
    "eta_to": 0.50,
    "eta_motor_esc": 0.82,
    "sm_range": (8.0, 15.0),
    "target_flight_time": 8.0,
    "time_fos": 1.2,
    "custom_speeds": {},
}

# Ensure every default key is guaranteed in st.session_state
for key, val in default_config.items():
    if key not in st.session_state:
        st.session_state[key] = val

# -------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------
def get_velocity_dict():
    """Returns the complete dictionary of defined generic speeds (m/s)."""
    # Use st.session_state.get() with fallback defaults to prevent AttributeErrors
    v_dict = {
        "Zero": 0.0,
        "V_stall": float(st.session_state.get("v_stall", 10.0)),
        "V_to": float(st.session_state.get("v_to", 11.0)),
        "V_to_avg": round(float(st.session_state.get("v_to", 11.0)) / math.sqrt(2.0), 3),
        "V_climb": float(st.session_state.get("v_climb", 13.0)),
        "V_cruise": float(st.session_state.get("v_cruise", 16.0)),
        "V_vert_climb": float(st.session_state.get("climb_rate", 3.0)),
        "V_descent": float(st.session_state.get("v_descent", 3.0)),
    }

    # Safe retrieval of custom_speeds regardless of whether it's dict, list, or missing
    custom = st.session_state.get("custom_speeds", {})
    if isinstance(custom, dict):
        for k, v in custom.items():
            try:
                v_dict[str(k)] = float(v)
            except (ValueError, TypeError):
                continue
    elif isinstance(custom, list):
        # Handles case where UI component saved it as list of tuples or dicts
        for item in custom:
            if isinstance(item, dict) and "name" in item and "value" in item:
                try:
                    v_dict[str(item["name"])] = float(item["value"])
                except (ValueError, TypeError):
                    continue

    return v_dict


def get_effective_target_time():
    t_flight = float(st.session_state.get("target_flight_time", 8.0))
    fos = float(st.session_state.get("time_fos", 1.2))
    return t_flight * fos


# -------------------------------------------------------------
# MISSION SEGMENTS INITIALIZATION
# -------------------------------------------------------------
if "mission_segments" not in st.session_state:
    eff_t0 = get_effective_target_time()
    v_map0 = get_velocity_dict()

    default_specs = [
        {
            "id": 0,
            "name": "Takeoff",
            "v_forward": "V_to_avg",
            "v_vert": "Zero",
            "time_frac": 0.05,
            "pct_banked": 0.0,
            "bank_angle": 0.0,
            "altitude": 0.0,
            "loaded": True,
            "ground_roll": True,
        },
        {
            "id": 1,
            "name": "Climb",
            "v_forward": "V_climb",
            "v_vert": "V_vert_climb",
            "time_frac": 0.15,
            "pct_banked": 10.0,
            "bank_angle": 20.0,
            "altitude": 50.0,
            "loaded": True,
            "ground_roll": False,
        },
        {
            "id": 2,
            "name": "Cruise",
            "v_forward": "V_cruise",
            "v_vert": "Zero",
            "time_frac": 0.35,
            "pct_banked": 25.0,
            "bank_angle": 35.0,
            "altitude": 100.0,
            "loaded": True,
            "ground_roll": False,
        },
        {
            "id": 3,
            "name": "Drop Load",
            "v_forward": "V_cruise",
            "v_vert": "Zero",
            "time_frac": 0.05,
            "pct_banked": 0.0,
            "bank_angle": 0.0,
            "altitude": 80.0,
            "loaded": False,
            "ground_roll": False,
        },
        {
            "id": 4,
            "name": "RTB",
            "v_forward": "V_cruise",
            "v_vert": "Zero",
            "time_frac": 0.30,
            "pct_banked": 25.0,
            "bank_angle": 35.0,
            "altitude": 80.0,
            "loaded": False,
            "ground_roll": False,
        },
        {
            "id": 5,
            "name": "Land",
            "v_forward": "V_stall",
            "v_vert": "V_descent",
            "time_frac": 0.10,
            "pct_banked": 0.0,
            "bank_angle": 0.0,
            "altitude": 0.0,
            "loaded": False,
            "ground_roll": False,
        },
    ]

    for seg in default_specs:
        t_s = round(seg["time_frac"] * eff_t0, 2)
        vf = abs(v_map0.get(seg["v_forward"], 0.0))
        d_m = round(vf * t_s, 2)
        seg["time_m"] = t_s
        seg["distance_m"] = d_m
        sid = seg["id"]

        st.session_state[f"seg_name_{sid}"] = seg["name"]
        st.session_state[f"seg_vfwd_{sid}"] = seg["v_forward"]
        st.session_state[f"seg_vvert_{sid}"] = seg["v_vert"]
        st.session_state[f"seg_frac_{sid}"] = float(seg["time_frac"])
        st.session_state[f"seg_time_{sid}"] = float(seg["time_m"])
        st.session_state[f"seg_dist_{sid}"] = float(seg["distance_m"])
        st.session_state[f"seg_pbank_{sid}"] = float(seg["pct_banked"])
        st.session_state[f"seg_bank_{sid}"] = float(seg["bank_angle"])
        st.session_state[f"seg_alt_{sid}"] = float(seg["altitude"])
        st.session_state[f"seg_loaded_{sid}"] = bool(seg["loaded"])
        st.session_state[f"seg_ground_{sid}"] = bool(seg["ground_roll"])

    st.session_state["mission_segments"] = default_specs

# Callbacks for mission segment synchronization
def sync_all_segments_from_global():
    """Recalculates segment times & distances when effective target time or generic speeds change."""
    eff_t = get_effective_target_time()
    v_map = get_velocity_dict()
    for seg in st.session_state.mission_segments:
        sid = seg["id"]
        frac = float(st.session_state.get(f"seg_frac_{sid}", seg["time_frac"]))
        vf_key = st.session_state.get(f"seg_vfwd_{sid}", seg["v_forward"])
        if vf_key not in v_map:
            vf_key = "V_cruise"
            st.session_state[f"seg_vfwd_{sid}"] = vf_key
        vf = abs(v_map.get(vf_key, 0.0))
        new_t = round(frac * eff_t, 2)
        new_d = round(vf * new_t, 2)
        st.session_state[f"seg_time_{sid}"] = float(new_t)
        st.session_state[f"seg_dist_{sid}"] = float(new_d)
        seg["time_frac"] = frac
        seg["time_m"] = float(new_t)
        seg["distance_m"] = float(new_d)


def on_seg_frac_change(sid):
    eff_t = get_effective_target_time()
    v_map = get_velocity_dict()
    frac = float(st.session_state[f"seg_frac_{sid}"])
    vf_key = st.session_state[f"seg_vfwd_{sid}"]
    vf = abs(v_map.get(vf_key, 0.0))
    new_t = round(frac * eff_t, 2)
    new_d = round(vf * new_t, 2)
    st.session_state[f"seg_time_{sid}"] = float(new_t)
    st.session_state[f"seg_dist_{sid}"] = float(new_d)
    for seg in st.session_state.mission_segments:
        if seg["id"] == sid:
            seg["time_frac"] = frac
            seg["time_m"] = float(new_t)
            seg["distance_m"] = float(new_d)
            break


def on_seg_time_change(sid):
    eff_t = get_effective_target_time()
    v_map = get_velocity_dict()
    new_t = float(st.session_state[f"seg_time_{sid}"])
    vf_key = st.session_state[f"seg_vfwd_{sid}"]
    vf = abs(v_map.get(vf_key, 0.0))
    new_frac = round(new_t / eff_t, 4) if eff_t > 0 else 0.0
    new_d = round(vf * new_t, 2)
    st.session_state[f"seg_frac_{sid}"] = float(new_frac)
    st.session_state[f"seg_dist_{sid}"] = float(new_d)
    for seg in st.session_state.mission_segments:
        if seg["id"] == sid:
            seg["time_frac"] = float(new_frac)
            seg["time_m"] = new_t
            seg["distance_m"] = float(new_d)
            break


def on_seg_dist_change(sid):
    eff_t = get_effective_target_time()
    v_map = get_velocity_dict()
    new_d = float(st.session_state[f"seg_dist_{sid}"])
    vf_key = st.session_state[f"seg_vfwd_{sid}"]
    vf = abs(v_map.get(vf_key, 0.0))
    if vf > 1e-6:
        new_t = round(new_d / vf, 2)
        new_frac = round(new_t / eff_t, 4) if eff_t > 0 else 0.0
        st.session_state[f"seg_time_{sid}"] = float(new_t)
        st.session_state[f"seg_frac_{sid}"] = float(new_frac)
        for seg in st.session_state.mission_segments:
            if seg["id"] == sid:
                seg["time_frac"] = float(new_frac)
                seg["time_m"] = float(new_t)
                seg["distance_m"] = new_d
                break
    else:
        st.warning(
            f"Segment forward velocity ({vf_key}) is 0 m/s; distance cannot drive time."
        )


def on_seg_vfwd_change(sid):
    v_map = get_velocity_dict()
    vf_key = st.session_state[f"seg_vfwd_{sid}"]
    vf = abs(v_map.get(vf_key, 0.0))
    cur_t = float(st.session_state[f"seg_time_{sid}"])
    new_d = round(vf * cur_t, 2)
    st.session_state[f"seg_dist_{sid}"] = float(new_d)
    for seg in st.session_state.mission_segments:
        if seg["id"] == sid:
            seg["v_forward"] = vf_key
            seg["distance_m"] = float(new_d)
            break


def add_mission_segment():
    sid = int(st.session_state.next_seg_id)
    st.session_state.next_seg_id = sid + 1
    eff_t = get_effective_target_time()
    v_map = get_velocity_dict()
    frac = 0.05
    t_s = round(frac * eff_t, 2)
    vf = abs(v_map.get("V_cruise", 16.0))
    d_m = round(vf * t_s, 2)
    new_seg = {
        "id": sid,
        "name": f"Segment {len(st.session_state.mission_segments) + 1}",
        "v_forward": "V_cruise",
        "v_vert": "Zero",
        "time_frac": frac,
        "time_m": t_s,
        "distance_m": d_m,
        "pct_banked": 0.0,
        "bank_angle": 0.0,
        "altitude": 100.0,
        "loaded": False,
        "ground_roll": False,
    }
    st.session_state[f"seg_name_{sid}"] = new_seg["name"]
    st.session_state[f"seg_vfwd_{sid}"] = new_seg["v_forward"]
    st.session_state[f"seg_vvert_{sid}"] = new_seg["v_vert"]
    st.session_state[f"seg_frac_{sid}"] = float(new_seg["time_frac"])
    st.session_state[f"seg_time_{sid}"] = float(new_seg["time_m"])
    st.session_state[f"seg_dist_{sid}"] = float(new_seg["distance_m"])
    st.session_state[f"seg_pbank_{sid}"] = float(new_seg["pct_banked"])
    st.session_state[f"seg_bank_{sid}"] = float(new_seg["bank_angle"])
    st.session_state[f"seg_alt_{sid}"] = float(new_seg["altitude"])
    st.session_state[f"seg_loaded_{sid}"] = bool(new_seg["loaded"])
    st.session_state[f"seg_ground_{sid}"] = bool(new_seg["ground_roll"])
    st.session_state.mission_segments.append(new_seg)


def remove_mission_segment(sid):
    st.session_state.mission_segments = [
        s for s in st.session_state.mission_segments if s["id"] != sid
    ]
    for prefix in [
        "seg_name_",
        "seg_vfwd_",
        "seg_vvert_",
        "seg_frac_",
        "seg_time_",
        "seg_dist_",
        "seg_pbank_",
        "seg_bank_",
        "seg_alt_",
        "seg_loaded_",
        "seg_ground_",
    ]:
        st.session_state.pop(f"{prefix}{sid}", None)

# Allow optional syncing rather than forcibly overwriting manual edits every rerun
if use_live_sheet:
    if st.sidebar.button("Sync Target MTOW with Live Sheet"):
        st.session_state["target_mtow"] = float(live_mtow_kg)

st.title("MES İHA Konsept Tasarım Aracı")

with st.sidebar:
    st.header("Statik Stabilite Marj Sınırları")
    sm_range = st.slider(
        "Allowable Static Margin Range (% MAC)",
        min_value=-5.0,
        max_value=35.0,
        step=0.5,
        key="sm_range",
    )
    sm_min, sm_max = sm_range

    eta_motor_esc = st.slider(
        "Motor & ESC Efficiency", 0.70, 0.95, step=0.01, key="eta_motor_esc"
    )


def update_cd0_callback():
    st.session_state.cd0_active = st.session_state.temp_recalc_cd0


#-------------------------------------------------------------
# STAGE 1: MISSION PROFILE & GENERIC VELOCITY DEFINITIONS
# -------------------------------------------------------------
st.header("1. Görev Profili ve Parametreler")

# --- 1.1 GENERIC VELOCITY DEFINITIONS ---
st.subheader("1.1 Generic Velocity Definitions")
st.caption(
    "Define reference velocities here. These populate the mission segment velocity dropdowns and drive the constraint sizing."
)

vcol1, vcol2, vcol3, vcol4, vcol5, vcol6 = st.columns(6)
with vcol1:
    v_stall = st.number_input(
        "V_stall (m/s)",
        min_value=1.0,
        step=0.5,
        key="v_stall",
        on_change=sync_all_segments_from_global,
    )
with vcol2:
    v_to = st.number_input(
        "V_to (Takeoff, m/s)",
        min_value=1.0,
        step=0.5,
        key="v_to",
        on_change=sync_all_segments_from_global,
    )
with vcol3:
    v_climb = st.number_input(
        "V_climb (m/s)",
        min_value=1.0,
        step=0.5,
        key="v_climb",
        on_change=sync_all_segments_from_global,
    )
with vcol4:
    v_cruise = st.number_input(
        "V_cruise (m/s)",
        min_value=1.0,
        step=0.5,
        key="v_cruise",
        on_change=sync_all_segments_from_global,
    )
with vcol5:
    climb_rate = st.number_input(
        "V_vert_climb (m/s)",
        min_value=0.0,
        step=0.5,
        key="climb_rate",
        on_change=sync_all_segments_from_global,
    )
with vcol6:
    v_descent = st.number_input(
        "V_descent (m/s)",
        min_value=-20.0,
        max_value=0.0,
        step=0.5,
        key="v_descent",
        on_change=sync_all_segments_from_global,
    )

with st.expander("➕ Manage Custom Generic Velocities", expanded=False):
    c_spd1, c_spd2, c_spd3 = st.columns([2, 2, 1])
    with c_spd1:
        new_spd_name = st.text_input("New Velocity Name (e.g., V_loiter, V_dash)", value="")
    with c_spd2:
        new_spd_val = st.number_input("Velocity Value (m/s)", value=14.0, step=0.5)
    with c_spd3:
        st.write("")
        st.write("")
        if st.button("➕ Add / Update Speed"):
            clean_name = new_spd_name.strip()
            if clean_name:
                st.session_state.custom_speeds[clean_name] = float(new_spd_val)
                sync_all_segments_from_global()
                st.rerun()

    if st.session_state.custom_speeds:
        st.markdown("**Active Custom Velocities:**")
        for k_spd in list(st.session_state.custom_speeds.keys()):
            cs1, cs2, cs3 = st.columns([2, 2, 1])
            cs1.write(f"**{k_spd}**")
            updated_v = cs2.number_input(
                f"Value for {k_spd} (m/s)",
                value=float(st.session_state.custom_speeds[k_spd]),
                step=0.5,
                key=f"cust_spd_edit_{k_spd}",
            )
            if updated_v != st.session_state.custom_speeds[k_spd]:
                st.session_state.custom_speeds[k_spd] = float(updated_v)
                sync_all_segments_from_global()
            if cs3.button("🗑️ Remove", key=f"del_spd_{k_spd}"):
                del st.session_state.custom_speeds[k_spd]
                sync_all_segments_from_global()
                st.rerun()

velocity_dict = get_velocity_dict()
velocity_options = list(velocity_dict.keys())
velocity_summary_str = " | ".join([f"**{k}**: {v:.2f} m/s" for k, v in velocity_dict.items()])
st.caption(f"Available Speeds Pool: {velocity_summary_str}")

st.markdown("---")

# --- 1.2 AIRCRAFT, SURFACE, MANEUVER & ATMOSPHERE PARAMETERS ---
st.subheader("1.2 Aircraft, Surface, Maneuver & Aero Limits")
col_p1, col_p2, col_p3 = st.columns(3)

with col_p1:
    st.markdown("**Weights & Surface**")
    target_mtow = st.number_input("Target MTOW (kg)", min_value=0.1, step=0.1, key="target_mtow")
    s_g = st.number_input("Ground Run Limit S_g (m)", min_value=1.0, step=1.0, key="s_g")
    runway_friction = st.number_input(
        "Runway Friction Coefficient",
        min_value=0.0,
        max_value=1.0,
        step=0.01,
        key="runway_friction",
    )


def update_from_bank():
    phi_rad = np.radians(st.session_state.bank_angle)
    cos_phi = max(np.cos(phi_rad), 1e-4)
    st.session_state.n_turn = float(np.clip(round(1.0 / cos_phi, 2), 1.0, 5.75))


def update_from_load():
    n = max(st.session_state.n_turn, 1.0)
    phi_deg = np.degrees(np.arccos(1.0 / n))
    st.session_state.bank_angle = float(np.clip(round(phi_deg, 1), 0.0, 80.0))


with col_p2:
    st.markdown("**Climb & Maneuver Limits**")
    # Safe clamping for arcsin to prevent domain crash when climb_rate > v_climb
    climb_ratio = np.clip(climb_rate / v_climb, -1.0, 1.0)
    climb_angle = np.degrees(np.arcsin(climb_ratio))
    if climb_rate >= v_climb:
        st.warning("Rate of climb cannot exceed or equal climb airspeed!")
    st.caption(f"Tırmanma Açısı (γ): **{climb_angle:.1f}°**")

    bank_angle = st.slider(
        "Sizing Max Bank Angle φ (°)",
        min_value=0.0,
        max_value=80.0,
        step=1.0,
        key="bank_angle",
        on_change=update_from_bank,
    )

    n_turn = st.slider(
        "Sustained Turn Load Factor n",
        min_value=1.0,
        max_value=5.75,
        step=0.05,
        key="n_turn",
        on_change=update_from_load,
    )

with col_p3:
    st.markdown("**Atmosphere & Aero Limits**")
    cl_max_est = st.number_input("Estimated CL_max", min_value=0.1, step=0.05, key="cl_max_est")
    rho = st.number_input("Density rho (kg/m^3)", min_value=0.1, format="%.3f", key="rho")
    mu_air = st.number_input("Dynamic Viscosity mu (kg/(m*s))", format="%.3e", key="mu_air")
    g = st.number_input("Gravity g (m/s^2)", min_value=1.0, format="%.5f", key="g")
    cd0_active = st.number_input(
        "Parasite Drag CD0", min_value=0.001, format="%.4f", key="cd0_active"
    )
    cd_to = st.number_input(
        "Takeoff CD (CD_to)", min_value=0.001, step=0.005, format="%.3f", key="cd_to"
    )
    cl_to = st.number_input("Takeoff CL (CL_to)", min_value=0.01, step=0.05, key="cl_to")

st.markdown("---")

# --- 1.3 MISSION PROPERTIES & INTERACTIVE MISSION SEGMENTS ---
st.subheader("1.3 Mission Properties & Segments Builder")

mp_col1, mp_col2, mp_col3, mp_col4 = st.columns(4)
with mp_col1:
    target_flight_time = st.number_input(
        "Target Flight Time Goal (s)",
        min_value=10.0,
        step=10.0,
        key="target_flight_time",
        on_change=sync_all_segments_from_global,
    )
with mp_col2:
    time_fos = st.number_input(
        "Mission Time FoS (Factor of Safety)",
        min_value=1.0,
        max_value=3.0,
        step=0.05,
        key="time_fos",
        on_change=sync_all_segments_from_global,
    )
effective_target_time = get_effective_target_time()
with mp_col3:
    st.metric(
        "Effective Target Time",
        f"{effective_target_time:.1f} s",
        f"{effective_target_time / 60.0:.2f} min",
    )
with mp_col4:
    st.metric(
        "Loaded / Unloaded Mass",
        f"{target_mtow:.2f} kg",
        f"After Drop: {max(0.1, target_mtow - payload_mass):.2f} kg",
    )

st.markdown(
    "**Mission Segments** *(Edit **Time Fraction**, **Time (min)**, or **Distance (m)**)*"
)

for idx, seg in enumerate(list(st.session_state.mission_segments)):
    sid = seg["id"]
    if st.session_state.get(f"seg_vfwd_{sid}") not in velocity_options:
        st.session_state[f"seg_vfwd_{sid}"] = "V_cruise"
    if st.session_state.get(f"seg_vvert_{sid}") not in velocity_options:
        st.session_state[f"seg_vvert_{sid}"] = "Zero"

    with st.container(border=True):
        r1_c1, r1_c2, r1_c3, r1_c4, r1_c5, r1_c6 = st.columns([2.0, 1.5, 1.5, 1.1, 0.9, 0.5])
        with r1_c1:
            seg["name"] = st.text_input(
                f"#{idx + 1} Segment Name",
                key=f"seg_name_{sid}",
            )
        with r1_c2:
            seg["v_forward"] = st.selectbox(
                f"v_forward ({velocity_dict.get(st.session_state[f'seg_vfwd_{sid}'], 0.0):.1f} m/s)",
                options=velocity_options,
                key=f"seg_vfwd_{sid}",
                on_change=on_seg_vfwd_change,
                args=(sid,),
            )
        with r1_c3:
            seg["v_vert"] = st.selectbox(
                f"v_vert ({velocity_dict.get(st.session_state[f'seg_vvert_{sid}'], 0.0):.1f} m/s)",
                options=velocity_options,
                key=f"seg_vvert_{sid}",
            )
        with r1_c4:
            seg["altitude"] = st.number_input(
                "Altitude (m)",
                min_value=0.0,
                max_value=5000.0,
                step=10.0,
                key=f"seg_alt_{sid}",
            )
        with r1_c5:
            seg["loaded"] = st.checkbox(
                "Loaded?",
                key=f"seg_loaded_{sid}",
                help="Checked = MTOW; Unchecked = MTOW - Payload Mass",
            )
            seg["ground_roll"] = st.checkbox(
                "Ground Roll?",
                key=f"seg_ground_{sid}",
                help="Check for takeoff ground run friction & acceleration.",
            )
        with r1_c6:
            st.write("")
            if st.button("🗑️", key=f"del_seg_btn_{sid}", help="Remove Segment"):
                remove_mission_segment(sid)
                st.rerun()

        r2_c1, r2_c2, r2_c3, r2_c4, r2_c5 = st.columns(5)
        with r2_c1:
            seg["time_frac"] = st.number_input(
                "Time Fraction (of Eff. Time)",
                min_value=0.0,
                max_value=5.0,
                step=0.01,
                format="%.4f",
                key=f"seg_frac_{sid}",
                on_change=on_seg_frac_change,
                args=(sid,),
            )
        with r2_c2:
            seg["time_m"] = st.number_input(
                "Time (s)",
                min_value=0.0,
                step=1.0,
                format="%.2f",
                key=f"seg_time_{sid}",
                on_change=on_seg_time_change,
                args=(sid,),
            )
        with r2_c3:
            seg["distance_m"] = st.number_input(
                "Distance (m)",
                min_value=0.0,
                step=10.0,
                format="%.2f",
                key=f"seg_dist_{sid}",
                on_change=on_seg_dist_change,
                args=(sid,),
            )
        with r2_c4:
            seg["pct_banked"] = st.number_input(
                "% Banked Travel",
                min_value=0.0,
                max_value=100.0,
                step=5.0,
                format="%.1f",
                key=f"seg_pbank_{sid}",
            )
        with r2_c5:
            seg["bank_angle"] = st.number_input(
                "Segment Bank Angle φ (°)",
                min_value=0.0,
                max_value=75.0,
                step=5.0,
                format="%.1f",
                key=f"seg_bank_{sid}",
            )

btn_col1, btn_col2, btn_col3 = st.columns([1.5, 2.0, 3.5])
with btn_col1:
    if st.button("➕ Add Mission Segment", use_container_width=True):
        add_mission_segment()
        st.rerun()

total_frac_sum = sum(s["time_frac"] for s in st.session_state.mission_segments)
total_mission_time_m = sum(s["time_m"] for s in st.session_state.mission_segments)
total_mission_dist_m = sum(s["distance_m"] for s in st.session_state.mission_segments)

with btn_col2:
    if st.button("Normalize Time Fractions to 1.0", use_container_width=True):
        if total_frac_sum > 0:
            for s in st.session_state.mission_segments:
                sid = s["id"]
                s["time_frac"] = round(s["time_frac"] / total_frac_sum, 4)
                st.session_state[f"seg_frac_{sid}"] = float(s["time_frac"])
            sync_all_segments_from_global()
            st.rerun()

with btn_col3:
    if abs(total_frac_sum - 1.0) > 0.01:
        st.warning(
            f"Total Time Fraction = **{total_frac_sum:.3f}** ({total_frac_sum * 100:.1f}% of Eff. Target Time) | "
            f"Scheduled Time: **{total_mission_time_m:.1f} s** ({total_mission_time_m / 60.0:.2f} min) | "
            f"Total Distance: **{total_mission_dist_m:,.1f} m**"
        )
    else:
        st.success(
            f"Total Time Fraction = **{total_frac_sum:.3f}** (100% of Eff. Target Time) | "
            f"Scheduled Time: **{total_mission_time_m:.1f} s** ({total_mission_time_m / 60.0:.2f} min) | "
            f"Total Distance: **{total_mission_dist_m:,.1f} m**"
        )
# -------------------------------------------------------------
# STAGE 2: P/W CONSTRAINT ANALYSIS
# -------------------------------------------------------------
st.header("2. Master Constraint Analysis (Power-to-Weight)")

eta_cruise = st.session_state.get("eta_cruise", 0.72)
eta_climb = st.session_state.get("eta_climb", 0.65)
eta_to = st.session_state.get("eta_to", 0.50)

col_ar, col_plot = st.columns([1, 2])

with col_ar:
    ar = st.slider("Aspect Ratio (AR)", 4.0, 12.0, 7.0, step=0.5)
    e0 = max(0.1, 1.14 - 0.0801 * (ar**0.68))
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
    tw_climb = (
        (climb_rate / v_climb)
        + (q_climb * cd0_active / ws_crit_pa)
        + (k_factor * ws_crit_pa / q_climb)
    )
    tw_turn = q_cruise * (
        (cd0_active / ws_crit_pa) + ws_crit_pa * k_factor * ((n_turn / q_cruise) ** 2)
    )
    tw_to = (
        ((v_to**2) / (2 * g * s_g))
        + (q_to_avg * cd_to / ws_crit_pa)
        + runway_friction * (1.0 - (q_to_avg * cl_to / ws_crit_pa))
    )

    pw_crit = {
        "Cruise": (tw_cruise * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Climb": (tw_climb * g * v_climb) / (eta_climb * eta_motor_esc),
        "Turn": (tw_turn * g * v_cruise) / (eta_cruise * eta_motor_esc),
        "Takeoff Ground Run": (tw_to * g * v_to_avg) / (eta_to * eta_motor_esc),
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
    tw_cl = (
        (climb_rate / v_climb)
        + (q_climb * cd0_active / ws_pa_range)
        + (k_factor * ws_pa_range / q_climb)
    )
    tw_t = q_cruise * (
        (cd0_active / ws_pa_range) + ws_pa_range * k_factor * ((n_turn / q_cruise) ** 2)
    )
    tw_to_curve = (
        ((v_to**2) / (2 * g * s_g))
        + (q_to_avg * cd_to / ws_pa_range)
        + runway_friction * (1.0 - (q_to_avg * cl_to / ws_pa_range))
    )

    pw_c = (tw_c * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_cl = (tw_cl * g * v_climb) / (eta_climb * eta_motor_esc)
    pw_t = (tw_t * g * v_cruise) / (eta_cruise * eta_motor_esc)
    pw_to_curve = (tw_to_curve * g * v_to_avg) / (eta_to * eta_motor_esc)

    fig_pw, ax = plt.subplots(figsize=(7, 4.5), dpi=100)
    ax.plot(ws_kgm2_range, pw_c, label="Cruise", color="crimson")
    ax.plot(ws_kgm2_range, pw_cl, label="Climb", color="royalblue")
    ax.plot(ws_kgm2_range, pw_t, label="Turn", color="purple")
    ax.plot(ws_kgm2_range, pw_to_curve, label="Takeoff", color="forestgreen")
    ax.axvline(x=opt_ws_kgm2, label="Stall Limit", color="black", linestyle="--", lw=1.5)
    ax.plot(
        opt_ws_kgm2,
        opt_pw,
        "ro",
        markersize=8,
        label=f"Design Point ({opt_ws_kgm2:.2f}, {opt_pw:.1f})",
    )

    upper_pw = np.maximum.reduce([pw_c, pw_cl, pw_t, pw_to_curve])
    feasible_mask = ws_kgm2_range <= opt_ws_kgm2
    ax.fill_between(
        ws_kgm2_range[feasible_mask],
        upper_pw[feasible_mask],
        max_pw_plot,
        color="lightgray",
        alpha=0.4,
    )

    ax.set_xlim(0, max_ws_plot)
    ax.set_ylim(0, max_pw_plot)
    ax.set_xlabel("Wing Loading W/S (kg/m^2)")
    ax.set_ylabel("Electrical Power Loading P/W (W/kg)")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8.0)
    st.pyplot(fig_pw)
    plt.close(fig_pw)

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

    active_kv = 1000.0
    active_max_amp = 30.0
    active_max_power = 400.0
    active_ir_mohm = 50.0

    if motor_type == "Database":
        if not df_motors.empty:
            df_motors_clean = df_motors.copy()
            df_motors_clean["Display"] = (
                df_motors_clean["Üretici"].astype(str)
                + " "
                + df_motors_clean["Model"].astype(str)
                + " - "
                + df_motors_clean["KV"].astype(str)
                + "KV"
            )

            motor_choice = st.selectbox("Select a Motor", df_motors_clean["Display"].dropna().unique())
            motor_data = df_motors_clean[df_motors_clean["Display"] == motor_choice].iloc[0]

            active_kv = safe_get_coef(motor_data, "KV", 1000.0)
            active_max_amp = safe_get_coef(motor_data, "Max. Amper", 30.0)
            active_max_power = safe_get_coef(motor_data, "Max. Güç", 400.0)
            active_ir_mohm = safe_get_coef(motor_data, "İç Direnç (mOhm)", 50.0)

            st.info(
                f"**Weight:** {motor_data.get('Ağırlık', 'N/A')} g  \n"
                f"**Max Power:** {active_max_power} W  \n"
                f"**Max Amps:** {active_max_amp} A  \n"
                f"**Internal Res:** {active_ir_mohm} mΩ"
            )

            prop_cols = motor_data.index[10:]
            valid_props = motor_data[prop_cols].dropna().astype(str).tolist()
            selected_motor_compatible_props = [
                p.strip() for p in valid_props if p.strip() and p.strip().lower() != "nan"
            ]
        else:
            st.warning("Motor database is empty or could not be loaded.")
    else:
        active_kv = st.number_input("KV Rating", min_value=10.0, value=1000.0, step=50.0)
        c_motor_weight = st.number_input("Motor Weight (g)", min_value=1.0, value=100.0, step=5.0)
        active_max_amp = st.number_input("Max Continuous Current (A)", min_value=1.0, value=30.0, step=1.0)
        active_max_power = st.number_input("Max Power (W)", min_value=1.0, value=400.0, step=10.0)
        active_ir_mohm = st.number_input("Internal Resistance (mOhm)", min_value=1.0, value=50.0, step=1.0)

    # --- RPM Math & Current Slider ---
    st.markdown("**Motor RPM Performance**")
    v_batt_nominal = batt_s * 3.7
    rpm_empty = active_kv * v_batt_nominal

    st.metric("Empty RPM (Nominal Voltage)", f"{rpm_empty:,.0f} RPM", f"{v_batt_nominal:.1f} V")

    sim_current = st.slider(
        "Simulate Current Draw (A)", 0.0, float(active_max_amp), value=float(active_max_amp) / 2.0
    )
    v_drop = sim_current * (active_ir_mohm / 1000.0)
    rpm_loaded = active_kv * max(0.0, v_batt_nominal - v_drop)

    st.metric(
        f"Loaded RPM @ {sim_current:.1f}A",
        f"{rpm_loaded:,.0f} RPM",
        f"Voltage Drop: -{v_drop:.2f} V",
        delta_color="inverse",
    )


# --- PROPELLER COLUMN ---
with col_el2:
    st.subheader("Propeller Selection")
    prop_type = st.radio("Propeller Definition", ["Database", "Custom"], horizontal=True)

    active_prop_dia_inch = 10.0
    ct_coeffs = [0.0, 0.0, -0.05, 0.100]  # a(J^3), b(J^2), c(J), d(const)
    cp_coeffs = [0.0, 0.0, -0.02, 0.050]

    if prop_type == "Database":
        if not df_props.empty:
            df_props_clean = df_props.copy()
            df_props_clean["Display"] = (
                df_props_clean["Üretici"].astype(str)
                + " "
                + df_props_clean["Diameter"].astype(str)
                + "x"
                + df_props_clean["Pitch"].astype(str)
            )

            if motor_type == "Database" and selected_motor_compatible_props:
                st.caption("Filtered to compatible propellers for selected motor:")
                prop_options = selected_motor_compatible_props
            else:
                prop_options = df_props_clean["Display"].dropna().unique()

            prop_choice = st.selectbox("Select a Propeller", prop_options)

            target_dia, target_pitch = parse_prop_string(prop_choice)
            prop_data = None

            if target_dia is not None and target_pitch is not None:
                for idx, row in df_props_clean.iterrows():
                    try:
                        db_dia = safe_get_coef(row, "Diameter", -1.0)
                        db_pitch = safe_get_coef(row, "Pitch", -1.0)
                        if abs(db_dia - target_dia) < 0.05 and abs(db_pitch - target_pitch) < 0.05:
                            prop_data = row
                            break
                    except Exception:
                        continue

            if prop_data is not None:
                active_prop_dia_inch = safe_get_coef(prop_data, "Diameter", 10.0)
                st.success(
                    f"Matched Profile: **{prop_data.get('Üretici', '')} {active_prop_dia_inch}x{prop_data.get('Pitch', '')}**"
                )

                raw_ct = [
                    safe_get_coef(prop_data, "CT vs J (a)"),
                    safe_get_coef(prop_data, "CT vs J (b)"),
                    safe_get_coef(prop_data, "CT vs J (c)"),
                    safe_get_coef(prop_data, "CT vs J (d)"),
                ]
                raw_cp = [
                    safe_get_coef(prop_data, "CP vs J (a)"),
                    safe_get_coef(prop_data, "CP vs J (b)"),
                    safe_get_coef(prop_data, "CP vs J (c)"),
                    safe_get_coef(prop_data, "CP vs J (d)"),
                ]

                # Auto-detect if sheet stores coefficients scaled by 1000 (static CT > 1.0 is unphysical)
                scale_factor = 1000.0 if abs(raw_ct[3]) > 1.0 else 1.0
                ct_coeffs = [c / scale_factor for c in raw_ct]
                cp_coeffs = [c / scale_factor for c in raw_cp]
            else:
                st.warning(
                    f"Extracted [Dia: {target_dia}\", Pitch: {target_pitch}\"] from '{prop_choice}', "
                    "but no exact match exists in the Propeller Database. Using fallback coefficients."
                )
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
st.markdown("Flight speeds and required thrusts are dynamically linked to **Stages 1 & 2**.")

w_n = target_mtow * g
t_req_cruise_calc = float(tw_cruise * w_n)
t_req_climb_calc = float(tw_climb * w_n)
t_req_to_calc = float(tw_to * w_n)

override_thrust = st.checkbox("Manually Override Required Thrusts?", value=False)

fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    st.info(f"**Takeoff Speed (Avg):** {v_to / math.sqrt(2):.2f} m/s (Liftoff: {v_to} m/s)")
    req_t_takeoff = st.number_input(
        "Req. Thrust Takeoff (N)",
        value=round(t_req_to_calc, 2),
        disabled=not override_thrust,
    )
    if not override_thrust:
        req_t_takeoff = t_req_to_calc

with fcol2:
    st.info(f"**Climb Speed:** {v_climb} m/s")
    req_t_climb = st.number_input(
        "Req. Thrust Climb (N)",
        value=round(t_req_climb_calc, 2),
        disabled=not override_thrust,
    )
    if not override_thrust:
        req_t_climb = t_req_climb_calc

with fcol3:
    st.info(f"**Cruise Speed:** {v_cruise} m/s")
    req_t_cruise = st.number_input(
        "Req. Thrust Cruise (N)",
        value=round(t_req_cruise_calc, 2),
        disabled=not override_thrust,
    )
    if not override_thrust:
        req_t_cruise = t_req_cruise_calc


def calc_poly(coeffs, j):
    return coeffs[0] * (j**3) + coeffs[1] * (j**2) + coeffs[2] * j + coeffs[3]

D_m = active_prop_dia_inch * 0.0254

def find_operating_point(V, T_req, rpm_max, ct_cf, cp_cf):
    if rpm_max <= 100 or D_m <= 0:
        return None
    rpms = np.linspace(100, rpm_max, 1500)
    ns = rpms / 60.0

    Js = V / (ns * D_m)
    CTs = calc_poly(ct_cf, Js)
    CPs = calc_poly(cp_cf, Js)

    valid = (CTs > 0) & (CPs > 0)
    if not np.any(valid):
        return None

    ns, CTs, CPs, Js = ns[valid], CTs[valid], CPs[valid], Js[valid]

    T_avail = rho * (ns**2) * (D_m**4) * CTs

    if np.max(T_avail) < T_req:
        return None  # Max RPM reached, can't produce required thrust

    idx = np.argmin(np.abs(T_avail - T_req))
    P_aero = rho * (ns[idx] ** 3) * (D_m**5) * CPs[idx]
    P_elec = P_aero / eta_motor_esc
    eta = Js[idx] * (CTs[idx] / CPs[idx]) if CPs[idx] > 0 else 0.0

    return {"RPM": ns[idx] * 60.0, "J": Js[idx], "Power": P_elec, "Efficiency": eta}


stages = {
    "Takeoff": find_operating_point(v_to / math.sqrt(2), req_t_takeoff, rpm_empty, ct_coeffs, cp_coeffs),
    "Climb": find_operating_point(v_climb, req_t_climb, rpm_empty, ct_coeffs, cp_coeffs),
    "Cruise": find_operating_point(v_cruise, req_t_cruise, rpm_empty, ct_coeffs, cp_coeffs),
}

needs_rerun = False
stage_key_map = {"Takeoff": "eta_to", "Climb": "eta_climb", "Cruise": "eta_cruise"}
for stage_name, key in stage_key_map.items():
    if stages[stage_name] is not None:
        new_eta = float(stages[stage_name]["Efficiency"])
        if 0.05 < new_eta <= 1.0:
            old_eta = st.session_state.get(key, 0.7)
            if abs(new_eta - old_eta) > 0.015:
                st.session_state[key] = round(new_eta, 3)
                needs_rerun = True

if needs_rerun:
    st.rerun()

rcol1, rcol2, rcol3 = st.columns(3)
cols = [rcol1, rcol2, rcol3]

for i, (stage_name, data) in enumerate(stages.items()):
    with cols[i]:
        st.markdown(f"**{stage_name} Requirements**")
        if data is None:
            st.error(f"Cannot meet thrust. Max RPM ({rpm_empty:.0f}) reached or invalid curve.")
        else:
            p_color = "normal" if data["Power"] <= active_max_power else "inverse"
            if data["Power"] > active_max_power:
                st.error("**Power Exceeded Motor Max!**")

            st.metric("Req. RPM", f"{data['RPM']:,.0f} RPM")
            st.metric(
                "Electrical Power",
                f"{data['Power']:.1f} W",
                f"Limit: {active_max_power} W",
                delta_color=p_color,
            )
            st.metric("Prop Efficiency", f"{data['Efficiency']*100:.1f} %")

# --- Plot Efficiency Curve ---
j_plot = np.linspace(0.01, 1.2, 300)
ct_plot = calc_poly(ct_coeffs, j_plot)
cp_plot = calc_poly(cp_coeffs, j_plot)

valid_j = (ct_plot > 0) & (cp_plot > 0)
j_plot, ct_plot, cp_plot = j_plot[valid_j], ct_plot[valid_j], cp_plot[valid_j]
eta_plot = np.clip(j_plot * (ct_plot / cp_plot), 0.0, 1.0)

fig_prop = go.Figure()
fig_prop.add_trace(
    go.Scatter(
        x=j_plot,
        y=eta_plot * 100,
        mode="lines",
        name="Propeller Efficiency",
        line=dict(color="royalblue", width=3),
    )
)

colors = {"Takeoff": "red", "Climb": "orange", "Cruise": "green"}
for stage_name, data in stages.items():
    if data is not None:
        fig_prop.add_trace(
            go.Scatter(
                x=[data["J"]],
                y=[data["Efficiency"] * 100],
                mode="markers+text",
                name=stage_name,
                marker=dict(size=12, color=colors[stage_name], symbol="cross"),
                text=[stage_name],
                textposition="top center",
            )
        )

fig_prop.update_layout(
    title="Propeller Efficiency (η vs. J)",
    xaxis_title="Advance Ratio (J)",
    yaxis_title="Efficiency (%)",
    yaxis_range=[0, 100],
    hovermode="x unified",
)

st.plotly_chart(fig_prop, use_container_width=True)

# -------------------------------------------------------------
# STAGE 4: WING SIZING
# -------------------------------------------------------------
st.header("4. Wing Sizing")
wingspan_total = math.sqrt(ar * s_req)

col_w1, col_w2 = st.columns(2)

with col_w1:
    apply_taper = st.checkbox("Apply Wing Taper?", value=False)
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

    st.markdown("---")
    re_tip_threshold = st.slider(
        "Tip Reynolds Warning Threshold", min_value=50000, max_value=300000, value=100000, step=10000
    )

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
        st.warning(
            f"Flow Separation Risk: Tip Re ({re_tip:,.0f}) is below the {re_tip_threshold:,} threshold. "
            "Consider increasing tip chord or cruise speed."
        )

# -------------------------------------------------------------
# STAGE 5: AIRFOIL TRANSLATION
# -------------------------------------------------------------
st.header("5. Airfoil 2D Lift Requirements")
cl_3d_cruise = (2.0 * target_mtow * g) / (rho * (v_cruise**2) * s_req)
# Safe guard against denominator <= 0 for extreme low-AR cases
denom_cruise = max(0.1, 1.0 - (cl_3d_cruise / (math.pi * ar * e0)))
denom_stall = max(0.1, 1.0 - (cl_max_est / (math.pi * ar * e0)))
cl_2d_cruise = cl_3d_cruise / denom_cruise
cl_2d_stall = cl_max_est / denom_stall

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
        "Horizontal Moment Arm l_H (m)",
        0.40,
        1.20,
        0.65,
        step=0.05,
        help="Distance from Wing AC to Horizontal Tail AC (at tail MAC quarter-chord)",
    )
    v_h = st.slider("Volume Coefficient V_H", 0.40, 0.85, 0.60, step=0.05)

    s_h = (v_h * s_req * mac) / l_h

    if "ar_h" not in st.session_state:
        st.session_state.ar_h = 4.5
    # Always keep b_h consistent if s_h changes upstream, clamped to slider bounds
    st.session_state.b_h = float(
        np.clip(round(math.sqrt(st.session_state.ar_h * s_h), 3), 0.30, 2.00)
    )

    def sync_ht_from_ar():
        st.session_state.b_h = float(
            np.clip(round(math.sqrt(st.session_state.ar_h * s_h), 3), 0.30, 2.00)
        )

    def sync_ht_from_b():
        st.session_state.ar_h = float(
            np.clip(round((st.session_state.b_h**2) / s_h, 2), 2.0, 8.0)
        )

    col_ar_h, col_b_h = st.columns(2)
    with col_ar_h:
        ar_h = st.slider(
            "Aspect Ratio (AR_H)", 2.0, 8.0, step=0.1, key="ar_h", on_change=sync_ht_from_ar
        )
    with col_b_h:
        b_h = st.slider(
            "Span b_H (m)", 0.30, 2.00, step=0.01, key="b_h", on_change=sync_ht_from_b
        )

    taper_h = st.slider("Taper Ratio λ_H (c_tip / c_root)", 0.30, 1.00, 1.00, step=0.05)

    c_root_h = (2.0 * s_h) / (b_h * (1.0 + taper_h))
    c_tip_h = taper_h * c_root_h
    mac_h = (2.0 / 3.0) * c_root_h * ((1.0 + taper_h + taper_h**2) / (1.0 + taper_h))
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
        "Vertical Moment Arm l_V (m)",
        0.40,
        1.20,
        0.65,
        step=0.05,
        help="Distance from Wing AC to Vertical Tail AC",
    )

    v_v = st.slider(
        "Volume Coefficient V_V",
        min_value=0.020,
        max_value=0.060,
        value=0.040,
        step=0.005,
        format="%.3f",
    )

    s_v = (v_v * s_req * wingspan_total) / l_v

    if "ar_v" not in st.session_state:
        st.session_state.ar_v = 1.8
    st.session_state.b_v = float(
        np.clip(round(math.sqrt(st.session_state.ar_v * s_v), 3), 0.15, 1.20)
    )

    def sync_vt_from_ar():
        st.session_state.b_v = float(
            np.clip(round(math.sqrt(st.session_state.ar_v * s_v), 3), 0.15, 1.20)
        )

    def sync_vt_from_b():
        st.session_state.ar_v = float(
            np.clip(round((st.session_state.b_v**2) / s_v, 2), 0.8, 4.0)
        )

    col_ar_v, col_b_v = st.columns(2)
    with col_ar_v:
        ar_v = st.slider(
            "Fin Aspect Ratio (AR_V)", 0.8, 4.0, step=0.1, key="ar_v", on_change=sync_vt_from_ar
        )
    with col_b_v:
        b_v = st.slider(
            "Height / Span b_V (m)", 0.15, 1.20, step=0.01, key="b_v", on_change=sync_vt_from_b
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
    0.20,
    0.70,
    0.45,
    step=0.01,
    help="Represents dynamic pressure ratio, lift curve slope ratio, and downwash factor.",
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
    fuse_len = st.number_input("Fuselage Length (m)", min_value=0.1, value=0.90, step=0.05)
    fuse_dia = st.number_input("Fuselage Equivalent Diameter (m)", min_value=0.01, value=0.12, step=0.01)
    cf_fuse = st.number_input("Skin Friction Cf", min_value=0.0001, value=0.005, format="%.4f")
    cd_wing_emp = st.number_input("Wing & Tail Profile Drag", min_value=0.001, value=0.012, format="%.4f")

    st.markdown("**Landing Gear Frontal Areas (m²)**")

    gear_components = {
        "Regular wheel and tire": 0.25,
        "Second wheel and tire in tandem": 0.15,
        "Streamlined wheel and tire": 0.18,
        "Wheel and tire with fairing": 0.13,
        "Streamline strut (1/6 < t/c < 1/3)": 0.05,
        "Round strut or wire": 0.30,
        "Flat spring gear leg": 1.40,
        "Fork, bogey, irregular fitting": 1.20,
    }

    with st.expander("Configure Landing Gear Components", expanded=False):
        total_gear_dq = 0.0
        for name, dq_val in gear_components.items():
            area = st.number_input(
                f"{name} (D/q/A = {dq_val})", min_value=0.0, value=0.0, step=0.001, format="%.4f"
            )
            total_gear_dq += area * dq_val

    gear_cd = total_gear_dq / s_req if s_req > 0 else 0.0

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

    st.caption(
        f"Drag Share: Fuselage: **{pct_fuse:.1f}%** | Lifting Surfaces: **{pct_wing_emp:.1f}%** | Gear: **{pct_gear:.1f}%**"
    )

    if abs(total_recalc_cd0 - st.session_state.cd0_active) > 0.003:
        st.warning(f"Drag mismatch: Initial {st.session_state.cd0_active:.4f} vs Buildup {total_recalc_cd0:.4f}")
    else:
        st.success("Drag assumption aligns with geometry.")

st.session_state.temp_recalc_cd0 = float(total_recalc_cd0)
st.button("Update Stage 1 Drag Assumption and Recalculate", on_click=update_cd0_callback)
