"""AC_RL telemetry app: sends telemetry over UDP and accepts JSON commands via file or UDP."""

import ac
import acsys
import json
import time
import os
import sys
import random

# Ensure app directory and third_party are on sys.path so embedded runtime
# can find modules placed alongside the app.
here = os.path.dirname(__file__)
if here not in sys.path:
    sys.path.insert(0, here)
third = os.path.join(here, "third_party")
if third not in sys.path:
    sys.path.insert(0, third)

# Ensure compiled dlls (like _socket.pyd) from the game's dll64 folder are importable
dll64 = os.path.join(here, "dll64")
if dll64 not in sys.path:
    sys.path.insert(0, dll64)

try:
    import _socket as _socket_ext  # try to load _socket directly from dll64
except Exception:
    _socket_ext = None

from IS_ACUtil import *


# Module-level debug logger so startup/import errors can be recorded to disk
def file_log(msg):
    try:
        path = os.path.join(os.path.dirname(__file__), "AC_RL_debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        try:
            ac.log("[AC_RL] %s" % msg)
        except Exception:
            pass


try:
    import ac_api.car_info as car_info
    import ac_api.car_stats as car_stats
    import ac_api.input_info as input_info
    import ac_api.lap_info as lap_info
    import ac_api.session_info as session_info
    import ac_api.tyre_info as tyre_info
except Exception as e:
    tyre_info = car_info = car_stats = input_info = lap_info = session_info = None
    try:
        ac.log("[AC_RL] Could not import ac_api modules: %s" % e)
    except Exception:
        pass
    # Also write diagnostics to disk for easier debugging
    try:
        file_log("Could not import ac_api modules: %s" % e)
        file_log("sys.path: %s" % repr(sys.path))
    except Exception:
        pass


def safe_call(mod, name, *args, default=None):
    try:
        if mod is None:
            return default
        fn = getattr(mod, name, None)
        if fn is None:
            return default
        return fn(*args)
    except Exception as e:
        try:
            file_log(
                "safe_call error %s.%s: %s"
                % (getattr(mod, "__name__", str(mod)), name, e)
            )
        except Exception:
            pass
        return default


# --- Socket helpers -------------------------------------------------
def _create_udp_socket(bind=False, host="127.0.0.1", port=0, blocking=False):
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if bind:
            s.bind((host, int(port)))
        s.setblocking(bool(blocking))
        return s
    except Exception as e:
        try:
            file_log(
                "_create_udp_socket error host=%s port=%s bind=%s: %s"
                % (host, port, bind, e)
            )
        except Exception:
            pass
        return None


def _close_socket(sock, name=None):
    try:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
            try:
                file_log("Socket %s closed" % (name or "<socket>"))
            except Exception:
                pass
    except Exception:
        pass


def call_sendcmd(*args, **kwargs):
    """Safe wrapper around sendCMD provided by IS_ACUtil or fallback to ac.sendCommand if available."""
    try:
        fn = (
            globals().get("sendCMD")
            or globals().get("sendcmd")
            or getattr(ac, "sendCommand", None)
        )
        if callable(fn):
            return fn(*args, **kwargs)
    except Exception as e:
        try:
            file_log("call_sendcmd failed: %s" % e)
        except Exception:
            pass
    return None


# --------------------------------------------------------------------


appName = "AC_RL"
width, height = 800, 800


TELEMETRY_FILENAME = "AC_RL_telemetry.json"

# UDP telemetry defaults (can be overridden via AC_RL_input.json)
TELEMETRY_UDP_HOST = "127.0.0.1"
TELEMETRY_UDP_PORT = 9876
telemetry_sock = None
telemetry_addr = (TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT)

# Input UDP defaults (commands) - listens for JSON commands
INPUT_UDP_HOST = "127.0.0.1"
INPUT_UDP_PORT = 9877
input_sock = None
input_addr = (INPUT_UDP_HOST, INPUT_UDP_PORT)


def handle_input_data(cmd, path=None):
    """Process a dict of commands coming from file or socket.
    If path is provided and reset is handled, the function will write back reset=False to that file.
    """
    try:
        if not isinstance(cmd, dict):
            return
    except Exception:
        return

    try:
        global TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT, telemetry_addr, telemetry_sock
        global INPUT_UDP_HOST, INPUT_UDP_PORT, input_addr, input_sock

        if "telemetry_udp_host" in cmd or "telemetry_udp_port" in cmd:
            host = cmd.get("telemetry_udp_host", TELEMETRY_UDP_HOST)
            port = cmd.get("telemetry_udp_port", TELEMETRY_UDP_PORT)
            try:
                port = int(port)
                TELEMETRY_UDP_HOST = host
                TELEMETRY_UDP_PORT = port
                telemetry_addr = (TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT)
                try:
                    file_log(
                        "Telemetry UDP target updated to %s:%d via input"
                        % telemetry_addr
                    )
                except Exception:
                    pass
            except Exception as e:
                try:
                    file_log("Invalid telemetry_udp_port in input: %s" % e)
                except Exception:
                    pass

        if "use_udp_telemetry" in cmd:
            use_udp = bool(cmd.get("use_udp_telemetry"))
            if not use_udp and telemetry_sock:
                _close_socket(telemetry_sock, "telemetry")
                telemetry_sock = None
                try:
                    file_log("Telemetry UDP disabled via input")
                except Exception:
                    pass
            elif use_udp and telemetry_sock is None:
                try:
                    telemetry_sock = _create_udp_socket(
                        bind=False,
                        host=TELEMETRY_UDP_HOST,
                        port=TELEMETRY_UDP_PORT,
                        blocking=True,
                    )
                    telemetry_addr = (TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT)
                    try:
                        file_log(
                            "Telemetry UDP enabled via input to %s:%d" % telemetry_addr
                        )
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        file_log("Could not enable telemetry UDP via input: %s" % e)
                    except Exception:
                        pass

        if "input_udp_host" in cmd or "input_udp_port" in cmd:
            host = cmd.get("input_udp_host", INPUT_UDP_HOST)
            port = cmd.get("input_udp_port", INPUT_UDP_PORT)
            try:
                port = int(port)
                INPUT_UDP_HOST = host
                INPUT_UDP_PORT = port
                input_addr = (INPUT_UDP_HOST, INPUT_UDP_PORT)
                # Rebind input socket if already open
                if input_sock:
                    try:
                        input_sock.close()
                    except Exception:
                        pass
                    input_sock = None
                try:
                    input_sock = _create_udp_socket(
                        bind=True,
                        host=INPUT_UDP_HOST,
                        port=INPUT_UDP_PORT,
                        blocking=False,
                    )
                    try:
                        file_log("Input UDP socket bound to %s:%d" % input_addr)
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        file_log("Could not bind input UDP socket: %s" % e)
                    except Exception:
                        pass
            except Exception as e:
                try:
                    file_log("Invalid input_udp_port in input: %s" % e)
                except Exception:
                    pass

        if "use_input_udp" in cmd:
            use_in = bool(cmd.get("use_input_udp"))
            if not use_in and input_sock:
                _close_socket(input_sock, "input")
                input_sock = None
                try:
                    file_log("Input UDP disabled via input")
                except Exception:
                    pass
            elif use_in and input_sock is None:
                try:
                    input_sock = _create_udp_socket(
                        bind=True,
                        host=INPUT_UDP_HOST,
                        port=INPUT_UDP_PORT,
                        blocking=False,
                    )
                    try:
                        file_log(
                            "Input UDP enabled via input to %s:%d"
                            % (INPUT_UDP_HOST, INPUT_UDP_PORT)
                        )
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        file_log("Could not enable input UDP via input: %s" % e)
                    except Exception:
                        pass

        if cmd.get("reset") is True:
            try:
                try:
                    call_sendcmd(68)
                    call_sendcmd(69)
                except Exception as e:
                    try:
                        file_log("Error calling reset command: %s" % e)
                    except Exception:
                        pass
            except Exception as e:
                try:
                    file_log("Error processing reset from input: %s" % e)
                except Exception:
                    pass

            if path:
                try:
                    cmd["reset"] = False
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(cmd, f)
                    try:
                        os.replace(tmp, path)
                    except Exception:
                        os.rename(tmp, path)
                except Exception as e:
                    try:
                        file_log("Error writing input file after reset: %s" % e)
                    except Exception:
                        pass
    except Exception:
        pass


def check_input_file():
    """
    Read AC_RL_input.json and if it contains {"reset": true} call the reset command via `call_sendcmd` and set reset to false.
    Also listens for JSON commands on the input UDP socket.
    """
    data = None
    path = None
    try:
        try:
            docs = ac.getDocumentsPath()
        except Exception:
            docs = os.path.dirname(__file__)
        path = os.path.join(docs, "AC_RL_input.json")
        if not os.path.exists(path):
            alt = os.path.join(os.path.dirname(__file__), "AC_RL_input.json")
            if os.path.exists(alt):
                path = alt
            else:
                # No file present; we'll still poll sockets for commands
                path = None
                data = None
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                try:
                    file_log("Error reading input file: %s" % e)
                except Exception:
                    pass
                data = None
    except Exception as e:
        try:
            file_log("Error determining input file path: %s" % e)
        except Exception:
            pass
        data = None

    # Process file-based commands if present
    if isinstance(data, dict):
        try:
            handle_input_data(data, path)
        except Exception:
            pass

    # Now poll the input UDP socket for any incoming commands
    if input_sock:
        try:
            while True:
                try:
                    pkt, addr = input_sock.recvfrom(65536)
                except (BlockingIOError, OSError) as e:
                    # No more data available
                    break
                try:
                    cmd = json.loads(pkt.decode("utf-8"))
                except Exception as e:
                    try:
                        file_log("Error parsing input UDP JSON from %s: %s" % (addr, e))
                    except Exception:
                        pass
                    continue

                try:
                    handle_input_data(cmd, None)
                except Exception:
                    pass
        except Exception as e:
            try:
                file_log("Error reading from input UDP socket: %s" % e)
            except Exception:
                pass


def acMain(ac_version):  # ----------------------------- App window Init

    # Don't forget to put anything you'll need to update later as a global variables
    global appWindow  # <- you'll need to update your window in other functions.

    appWindow = ac.newApp(appName)
    ac.setTitle(appWindow, appName)
    ac.setSize(appWindow, width, height)

    ac.addRenderCallback(
        appWindow, appGL
    )  # -> links this app's window to an OpenGL render function

    # ensure file_log exists (if import failed above we created it)
    try:
        file_log("acMain starting")
    except NameError:

        def file_log(msg):
            try:
                path = os.path.join(os.path.dirname(__file__), "AC_RL_debug.log")
                with open(path, "a", encoding="utf-8") as f:
                    f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
            except Exception:
                pass

        file_log("acMain starting")

    # Create UDP socket to send tyre telemetry
    try:
        file_log(
            "AC_RL telemetry configured for UDP %s:%d and file %s"
            % (TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT, TELEMETRY_FILENAME)
        )
    except Exception:
        pass

    # Try to create a UDP socket; prefer using the bundled _socket if available
    global telemetry_sock, telemetry_addr, input_sock, input_addr

    telemetry_sock = _create_udp_socket(
        bind=False, host=TELEMETRY_UDP_HOST, port=TELEMETRY_UDP_PORT, blocking=True
    )
    telemetry_addr = (TELEMETRY_UDP_HOST, TELEMETRY_UDP_PORT)
    if telemetry_sock:
        try:
            file_log("Telemetry UDP socket created to %s:%d" % telemetry_addr)
        except Exception:
            pass

    input_sock = _create_udp_socket(
        bind=True, host=INPUT_UDP_HOST, port=INPUT_UDP_PORT, blocking=False
    )
    input_addr = (INPUT_UDP_HOST, INPUT_UDP_PORT)
    if input_sock:
        try:
            file_log("Input UDP socket bound to %s:%d" % input_addr)
        except Exception:
            pass

    # Create labels to display tyre info (4 tyres)
    global tyre_labels
    tyre_labels = []
    for i in range(4):
        lbl = ac.addLabel(appWindow, "")
        ac.setPosition(lbl, 10, 20 + i * 120)
        ac.setSize(lbl, 380, 120)
        ac.setFontSize(lbl, 12)
        tyre_labels.append(lbl)

    # header
    hdr = ac.addLabel(appWindow, appName)
    ac.setPosition(hdr, 10, 0)
    ac.setFontSize(hdr, 16)

    return appName


def appGL(deltaT):  # -------------------------------- OpenGL UPDATE
    """
    This is where you redraw your openGL graphics
    if you need to use them .
    """
    # Call the update routine so we can log telemetry each frame
    acUpdate(deltaT)


def acUpdate(deltaT):  # -------------------------------- AC UPDATE
    """
    This is where you update your app window ( != OpenGL graphics )
    such as : labels , listener , ect ...
    """
    # Read input file for commands
    try:
        check_input_file()
    except Exception:
        pass

    # Read tyre telemetry from shared memory and update labels.
    if tyre_info is None:
        try:
            file_log("tyre_info is None; skipping update")
        except Exception:
            pass
        return

    try:
        for t in range(4):
            wear = tyre_info.get_tyre_wear_value(t)
            dirty = tyre_info.get_tyre_dirty(t)
            p = tyre_info.get_tyre_pressure(t)
            ti = tyre_info.get_tyre_temp(t, "i")
            tm = tyre_info.get_tyre_temp(t, "m")
            to = tyre_info.get_tyre_temp(t, "o")

            text = (
                "Tyre {idx} - Wear: {wear:.1f}%  Dirty: {dirty:.1f}\n"
                "Pressure: {p:.2f}  Temps (I/M/O): {ti:.1f}/{tm:.1f}/{to:.1f}"
            ).format(idx=t, wear=wear, dirty=dirty, p=p, ti=ti, tm=tm, to=to)

            ac.setText(tyre_labels[t], text)

        # Write telemetry JSON to a file in AC documents path for external process
        try:
            # Build full telemetry payload (session, car, inputs, lap, tyres, stats)
            tyres = []
            for t in range(4):
                tyres.append(
                    {
                        "index": t,
                        "wear": safe_call(
                            tyre_info, "get_tyre_wear_value", t, default=None
                        ),
                        "dirty": safe_call(
                            tyre_info, "get_tyre_dirty", t, default=None
                        ),
                        "pressure": safe_call(
                            tyre_info, "get_tyre_pressure", t, default=None
                        ),
                        "temp_i": safe_call(
                            tyre_info, "get_tyre_temp", t, "i", default=None
                        ),
                        "temp_m": safe_call(
                            tyre_info, "get_tyre_temp", t, "m", default=None
                        ),
                        "temp_o": safe_call(
                            tyre_info, "get_tyre_temp", t, "o", default=None
                        ),
                        "slip_ratio": safe_call(
                            tyre_info, "get_slip_ratio", t, default=None
                        ),
                        "slip_angle": safe_call(
                            tyre_info, "get_slip_angle", t, default=None
                        ),
                        # "load": safe_call(tyre_info, 'get_load', t, default=None),
                        "heading_vector": safe_call(
                            tyre_info, "get_tyre_heading_vector", t, default=None
                        ),
                        "angular_speed": safe_call(
                            tyre_info, "get_angular_speed", t, default=None
                        ),
                    }
                )

            session = {
                "session_type": safe_call(session_info, "get_session_type"),
                "driver_name": safe_call(session_info, "get_driver_name"),
                "track_name": safe_call(session_info, "get_track_name"),
                "track_config": safe_call(session_info, "get_track_config"),
                "track_length": safe_call(session_info, "get_track_length"),
                "cars_count": safe_call(session_info, "get_cars_count"),
                "session_status": safe_call(session_info, "get_session_status"),
                "air_temp": safe_call(session_info, "get_air_temp"),
                "road_temp": safe_call(session_info, "get_road_temp"),
                # 'tyre_compound': safe_call(session_info, 'get_tyre_compound'),
            }

            car = {
                "speed_kmh": safe_call(car_info, "get_speed", 0, "kmh"),
                "speed_mph": safe_call(car_info, "get_speed", 0, "mph"),
                "speed_ms": safe_call(car_info, "get_speed", 0, "ms"),
                "location": safe_call(car_info, "get_location", 0),
                "world_location": safe_call(car_info, "get_world_location", 0),
                "position": safe_call(car_info, "get_position", 0),
                "drs_available": safe_call(car_info, "get_drs_available"),
                "drs_enabled": safe_call(car_info, "get_drs_enabled"),
                "gear": safe_call(car_info, "get_gear", 0, True),
                "rpm": safe_call(car_info, "get_rpm", 0),
                "fuel": safe_call(car_info, "get_fuel"),
                "tyres_off_track": safe_call(car_info, "get_tyres_off_track"),
                "in_pit_lane": safe_call(car_info, "get_car_in_pit_lane"),
                "damage": safe_call(car_info, "get_total_damage"),
                "cg_height": safe_call(car_info, "get_cg_height", 0),
                "drive_train_speed": safe_call(car_info, "get_drive_train_speed", 0),
                "velocity": safe_call(car_info, "get_velocity"),
                "acceleration": safe_call(car_info, "get_acceleration"),
            }

            inputs = {
                "gas": safe_call(input_info, "get_gas_input", 0),
                "brake": safe_call(input_info, "get_brake_input", 0),
                "clutch": safe_call(input_info, "get_clutch", 0),
                "steer": safe_call(input_info, "get_steer_input", 0),
                "last_ff": safe_call(input_info, "get_last_ff", 0),
            }

            lap = {
                "get_current_lap_time": safe_call(
                    lap_info, "get_current_lap_time", 0, False
                ),
                "get_last_lap_time": safe_call(lap_info, "get_last_lap_time", 0, False),
                "get_best_lap_time": safe_call(lap_info, "get_best_lap_time", 0, False),
                "get_splits": safe_call(lap_info, "get_splits", 0, False),
                "get_split": safe_call(lap_info, "get_split"),
                "get_invalid": safe_call(lap_info, "get_invalid", 0),
                "get_lap_count": safe_call(lap_info, "get_lap_count", 0),
                "get_laps": safe_call(lap_info, "get_laps"),
                "get_lap_delta": safe_call(lap_info, "get_lap_delta", 0),
                "get_current_sector": safe_call(lap_info, "get_current_sector"),
            }

            stats = {
                "has_drs": safe_call(car_stats, "get_has_drs"),
                "has_ers": safe_call(car_stats, "get_has_ers"),
                "has_kers": safe_call(car_stats, "get_has_kers"),
                "abs_level": safe_call(car_stats, "abs_level"),
                "max_rpm": safe_call(car_stats, "get_max_rpm"),
                "max_fuel": safe_call(car_stats, "get_max_fuel"),
            }

            payload = {
                "app": appName,
                "timestamp": time.time(),
                "session": session,
                "car": car,
                "inputs": inputs,
                "lap": lap,
                "tyres": tyres,
                "stats": stats,
            }

            sent = False
            # Try to send telemetry over UDP first if we have a socket
            if telemetry_sock:
                try:
                    data_bytes = json.dumps(payload).encode("utf-8")
                    telemetry_sock.sendto(data_bytes, telemetry_addr)
                    sent = True
                except Exception as e:
                    try:
                        file_log("Error sending telemetry via UDP: %s" % e)
                    except Exception:
                        pass

            if not sent:
                # Fall back to writing telemetry JSON to a file in AC documents path
                try:
                    docs = ac.getDocumentsPath()
                except Exception:
                    docs = os.path.dirname(__file__)

                telemetry_path = os.path.join(docs, TELEMETRY_FILENAME)
                tmp_path = telemetry_path + ".tmp"
                try:
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f)
                    try:
                        os.replace(tmp_path, telemetry_path)
                    except Exception:
                        os.rename(tmp_path, telemetry_path)
                except Exception as e:
                    ac.log("[AC_RL] Error writing telemetry file: %s" % e)
                    try:
                        file_log("Error writing telemetry file: %s" % e)
                    except Exception:
                        pass
        except Exception as e:
            ac.log("[AC_RL] Error preparing telemetry payload: %s" % e)
            try:
                file_log("Error preparing telemetry payload: %s" % e)
            except Exception:
                pass
    except Exception as e:
        ac.log("[AC_RL] Error updating tyre labels: %s" % e)
        try:
            file_log("Error updating tyre labels: %s" % e)
        except Exception:
            pass


def acShutdown():
    """Cleanup socket on shutdown."""
    global telemetry_sock, input_sock
    try:
        if telemetry_sock:
            try:
                telemetry_sock.close()
            except Exception:
                pass
            telemetry_sock = None
            try:
                file_log("Telemetry socket closed")
            except Exception:
                pass
    except Exception:
        pass

    try:
        if input_sock:
            try:
                input_sock.close()
            except Exception:
                pass
            input_sock = None
            try:
                file_log("Input socket closed")
            except Exception:
                pass
    except Exception:
        pass
