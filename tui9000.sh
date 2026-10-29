#!/usr/bin/env bash
# nmtui-style Mihomo controller for Clash Verge Rev and compatible kernels.
set -u

APP_NAME="tui-9000"
TITLE="tui-9000 · Mihomo"
DEFAULT_ENDPOINT="http://127.0.0.1:9097"
DEFAULT_DELAY_URL="https://www.gstatic.com/generate_204"
DEFAULT_DELAY_TIMEOUT_MS=5000
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_DIR="$CONFIG_HOME/$APP_NAME"
DATA_DIR="$DATA_HOME/$APP_NAME"
SETTINGS_FILE="$CONFIG_DIR/settings.json"
STATE_FILE="$DATA_DIR/kernel.json"
LOG_FILE="$DATA_DIR/mihomo.log"

ENDPOINT="$DEFAULT_ENDPOINT"
SECRET=""
SAVED_SECRET=""
SECRET_OVERRIDE=""
SECRET_OVERRIDE_SET=0
MIHOMO_BINARY="mihomo"
MIHOMO_CONFIG=""
API_RESPONSE=""
API_ERROR=""

show_message() {
    whiptail --title "$TITLE" --msgbox "$1" "${2:-12}" "${3:-78}"
}

load_settings() {
    [[ -r "$SETTINGS_FILE" ]] || return 0
    if ! jq -e 'type == "object"' "$SETTINGS_FILE" >/dev/null 2>&1; then
        show_message "Settings file is not valid JSON:\n$SETTINGS_FILE\n\nUsing defaults."
        return 0
    fi
    ENDPOINT=$(jq -r --arg default "$DEFAULT_ENDPOINT" '.endpoint // $default | tostring' "$SETTINGS_FILE")
    SAVED_SECRET=$(jq -r '.secret // "" | tostring' "$SETTINGS_FILE")
    SECRET="$SAVED_SECRET"
    MIHOMO_BINARY=$(jq -r '.mihomo_binary // "mihomo" | tostring' "$SETTINGS_FILE")
    MIHOMO_CONFIG=$(jq -r '.config_file // "" | tostring' "$SETTINGS_FILE")
}

save_settings() {
    mkdir -p "$CONFIG_DIR" || return 1
    local temporary
    temporary=$(mktemp "$CONFIG_DIR/settings.XXXXXX") || return 1
    if ! jq -n \
        --arg endpoint "$ENDPOINT" \
        --arg secret "$SAVED_SECRET" \
        --arg mihomo_binary "$MIHOMO_BINARY" \
        --arg config_file "$MIHOMO_CONFIG" \
        '{endpoint:$endpoint, secret:$secret, mihomo_binary:$mihomo_binary, config_file:$config_file}' \
        >"$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    chmod 600 "$temporary" && mv -f "$temporary" "$SETTINGS_FILE"
}

api_request() {
    local method="$1" path="$2" body="${3-}" raw status response_message
    local -a args=(--silent --show-error --connect-timeout 3 --max-time 15 \
        --request "$method" --header 'Accept: application/json' --write-out $'\n%{http_code}')
    [[ -z "$SECRET" ]] || args+=(--header "Authorization: Bearer $SECRET")
    if [[ -n "$body" ]]; then
        args+=(--header 'Content-Type: application/json' --data "$body")
    fi

    if ! raw=$(curl "${args[@]}" "${ENDPOINT}${path}" 2>&1); then
        API_ERROR="Cannot reach $ENDPOINT: $raw"
        return 1
    fi
    status="${raw##*$'\n'}"
    API_RESPONSE="${raw%$'\n'*}"
    if [[ ! "$status" =~ ^2[0-9][0-9]$ ]]; then
        if [[ "$status" == 401 || "$status" == 403 ]]; then
            API_ERROR="Mihomo rejected the Bearer token. Check the secret in Clash Verge Rev → Settings → External Controller and tui-9000 Settings."
        else
            response_message=$(jq -r '.message // empty' <<<"$API_RESPONSE" 2>/dev/null || true)
            API_ERROR="Mihomo API returned HTTP $status: ${response_message:-$API_RESPONSE}"
        fi
        return 1
    fi
    API_ERROR=""
}

choose_menu() {
    local prompt="$1"; shift
    capture_dialog --menu "$prompt" 22 82 14 "$@"
}

capture_dialog() {
    whiptail --title "$TITLE" --output-fd 3 "$@" 3>&1 1>/dev/tty 2>/dev/tty
}

api_error_dialog() {
    show_message "${API_ERROR:-Mihomo API request failed.}"
}

show_status() {
    local version_data config_data version mode mixed_port
    if ! api_request GET /version; then api_error_dialog; return; fi
    version_data="$API_RESPONSE"
    if ! api_request GET /configs; then api_error_dialog; return; fi
    config_data="$API_RESPONSE"
    version=$(jq -r '.version // "unknown"' <<<"$version_data")
    mode=$(jq -r '.mode // "unknown"' <<<"$config_data")
    mixed_port=$(jq -r '.["mixed-port"] // "not reported"' <<<"$config_data")
    show_message "Connected to Mihomo\n\nAPI: $ENDPOINT\nVersion: $version\nMode: $mode\nMixed port: $mixed_port\nManaged kernel: $(managed_kernel_status)" 15 78
}

proxy_groups() {
    local data row group current member encoded payload action url timeout encoded_member encoded_url delay
    local -a options=() rows=() members=()
    if ! api_request GET /proxies; then api_error_dialog; return; fi
    data="$API_RESPONSE"
    mapfile -t rows < <(jq -r '
        .proxies // {} | to_entries[]
        | select((.value.type == "Selector" or .value.type == "URLTest" or .value.type == "Fallback" or .value.type == "LoadBalance" or .value.type == "Relay") and ((.value.all // []) | length > 0))
        | [.key, (.value.now // "")] | @tsv
    ' <<<"$data")
    for row in "${rows[@]}"; do
        IFS=$'\t' read -r group current <<<"$row"
        options+=("$group" "${current:-no selection}")
    done
    if ((${#options[@]} == 0)); then show_message "No selectable proxy groups were found."; return; fi
    group=$(choose_menu "Select a proxy group:" "${options[@]}") || return
    mapfile -t members < <(jq -r --arg group "$group" '.proxies[$group].all[]?' <<<"$data")
    options=()
    current=$(jq -r --arg group "$group" '.proxies[$group].now // ""' <<<"$data")
    for member in "${members[@]}"; do
        if [[ "$member" == "$current" ]]; then options+=("$member" "● current"); else options+=("$member" ""); fi
    done
    member=$(choose_menu "$group · current: ${current:-unknown}\n\nChoose a proxy:" "${options[@]}") || return
    action=$(choose_menu "$group → $member\n\nChoose an action:" \
        delay "Test delay" \
        select "Select proxy" \
        back "Back") || return
    case "$action" in
        back) return ;;
        select)
            if [[ "$member" == "$current" ]]; then show_message "$group is already using:\n\n$member"; return; fi
            encoded=$(jq -rn --arg value "$group" '$value | @uri')
            payload=$(jq -cn --arg name "$member" '{name:$name}')
            if api_request PUT "/proxies/$encoded" "$payload"; then show_message "$group is now using:\n\n$member"; else api_error_dialog; fi
            ;;
        delay)
            url=$(capture_dialog --inputbox "Delay-test URL:" 10 78 "$DEFAULT_DELAY_URL") || return
            [[ -n "$url" ]] || { show_message "Delay-test URL cannot be empty."; return; }
            timeout=$(capture_dialog --inputbox "Timeout in milliseconds:" 10 78 "$DEFAULT_DELAY_TIMEOUT_MS") || return
            [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || { show_message "Timeout must be a positive number of milliseconds."; return; }
            encoded_member=$(jq -rn --arg value "$member" '$value | @uri')
            encoded_url=$(jq -rn --arg value "$url" '$value | @uri')
            if ! api_request GET "/proxies/$encoded_member/delay?url=$encoded_url&timeout=$timeout"; then api_error_dialog; return; fi
            delay=$(jq -r '.delay // "unknown"' <<<"$API_RESPONSE")
            show_message "$member\n\nDelay: $delay ms\nTest URL: $url"
            ;;
        *) show_message "Unexpected proxy action: $action" ;;
    esac
}

show_traffic() {
    local raw status first_line curl_status upload download upload_total download_total
    local -a args=(--silent --show-error --no-buffer --connect-timeout 3 --max-time 3 \
        --header 'Accept: application/json' --write-out $'\n%{http_code}')
    [[ -z "$SECRET" ]] || args+=(--header "Authorization: Bearer $SECRET")
    if raw=$(curl "${args[@]}" "${ENDPOINT}/traffic" 2>/dev/null); then curl_status=0; else curl_status=$?; fi
    status="${raw##*$'\n'}"
    first_line="${raw%%$'\n'*}"
    if [[ "$status" == 401 || "$status" == 403 ]]; then
        show_message "Mihomo rejected the Bearer token. Check the Clash Verge Rev External Controller secret."
        return
    fi
    if [[ "$status" != 200 ]] || ! jq -e 'type == "object" and has("up") and has("down")' <<<"$first_line" >/dev/null 2>&1; then
        if ((curl_status != 0)) && [[ -z "$first_line" ]]; then
            show_message "Cannot read traffic from $ENDPOINT. Check the controller connection and try again."
        else
            show_message "Mihomo returned an unexpected traffic response:\n$first_line"
        fi
        return
    fi
    upload=$(jq -r '.up // 0' <<<"$first_line")
    download=$(jq -r '.down // 0' <<<"$first_line")
    upload_total=$(jq -r '.upTotal // 0' <<<"$first_line")
    download_total=$(jq -r '.downTotal // 0' <<<"$first_line")
    show_message "Current traffic (sampled now)\n\nUpload: $upload B/s  ·  Total: $upload_total B\nDownload: $download B/s  ·  Total: $download_total B"
}

routing_mode() {
    local config current selected
    if ! api_request GET /configs; then api_error_dialog; return; fi
    config="$API_RESPONSE"
    current=$(jq -r '.mode // "unknown"' <<<"$config")
    selected=$(choose_menu "Current mode: $current\n\nSelect a routing mode:" \
        rule "$([[ "$current" == rule ]] && printf '● current')" \
        global "$([[ "$current" == global ]] && printf '● current')" \
        direct "$([[ "$current" == direct ]] && printf '● current')") || return
    [[ "$selected" == "$current" ]] && return
    if api_request PATCH /configs "$(jq -cn --arg mode "$selected" '{mode:$mode}')"; then
        show_message "Routing mode set to $selected."
    else
        api_error_dialog
    fi
}

connection_label() {
    jq -r '
        .metadata as $m
        | ((($m.host // $m.destinationIP // "unknown destination")|tostring)
          + " · " + (($m.process // $m.processPath // "unknown process")|tostring)
          + " (" + (($m.network // "?")|tostring) + ") · "
          + ((.chains // ["DIRECT"]) | join(" → ")))
        | .[0:180]
    ' <<<"$1"
}

active_connections() {
    local data row id label selected encoded
    local -a options=() rows=()
    if ! api_request GET /connections; then api_error_dialog; return; fi
    data="$API_RESPONSE"
    mapfile -t rows < <(jq -c '.connections[]?' <<<"$data")
    if ((${#rows[@]} == 0)); then show_message "There are no active connections."; return; fi
    for row in "${rows[@]}"; do
        id=$(jq -r '.id // empty' <<<"$row")
        [[ -n "$id" ]] || continue
        label=$(connection_label "$row")
        options+=("$id" "$label")
    done
    if ((${#options[@]} == 0)); then show_message "No closable active connections were found."; return; fi
    selected=$(choose_menu "Select a connection to close:" "${options[@]}") || return
    if ! whiptail --title "$TITLE" --yesno "Close this connection?\n\n$selected" 10 78; then return; fi
    encoded=$(jq -rn --arg value "$selected" '$value | @uri')
    if api_request DELETE "/connections/$encoded"; then show_message "Connection closed."; else api_error_dialog; fi
}

resolve_binary() {
    local configured="$MIHOMO_BINARY" candidate
    if [[ "$configured" == */* ]]; then
        candidate="${configured/#\~/$HOME}"
        [[ -x "$candidate" ]] || return 1
    else
        candidate=$(command -v "$configured") || return 1
    fi
    readlink -f "$candidate"
}

managed_process_matches() {
    local pid="$1" binary="$2" config="$3" home="$4" actual arg
    local has_config=0 has_home=0
    [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
    actual=$(readlink -f "/proc/$pid/exe" 2>/dev/null) || return 1
    [[ "$actual" == "$binary" ]] || return 1
    local -a args=()
    mapfile -d '' -t args <"/proc/$pid/cmdline" || true
    for ((i=0; i<${#args[@]}; i++)); do
        arg="${args[$i]}"
        [[ "$arg" == -f && "${args[$((i+1))]-}" == "$config" ]] && has_config=1
        [[ "$arg" == -d && "${args[$((i+1))]-}" == "$home" ]] && has_home=1
    done
    ((has_config && has_home))
}

managed_state() {
    [[ -r "$STATE_FILE" ]] || return 1
    jq -e '(.pid | type == "number") and (.binary | type == "string") and (.config_file | type == "string") and (.home | type == "string")' "$STATE_FILE" >/dev/null 2>&1
}

managed_kernel_status() {
    local pid binary config home
    managed_state || { printf 'not running'; return; }
    pid=$(jq -r '.pid' "$STATE_FILE")
    binary=$(jq -r '.binary' "$STATE_FILE")
    config=$(jq -r '.config_file' "$STATE_FILE")
    home=$(jq -r '.home' "$STATE_FILE")
    if managed_process_matches "$pid" "$binary" "$config" "$home"; then printf 'running (PID %s)' "$pid"; else printf 'not running'; fi
}

start_kernel() {
    local binary config home validation pid
    binary=$(resolve_binary) || { show_message "Mihomo executable not found: $MIHOMO_BINARY"; return; }
    config="${MIHOMO_CONFIG/#\~/$HOME}"
    config=$(readlink -f "$config" 2>/dev/null) || config=""
    [[ -n "$config" && -f "$config" ]] || { show_message "Set a valid Mihomo YAML config file in Settings before starting the kernel."; return; }
    if [[ "$(managed_kernel_status)" == running* ]]; then show_message "A kernel started by tui-9000 is already running."; return; fi
    home="$DATA_DIR/kernel-home"
    mkdir -p "$home" "$DATA_DIR" || { show_message "Cannot create the tui-9000 data directory."; return; }
    if ! validation=$("$binary" -t -f "$config" -d "$home" 2>&1); then
        show_message "Mihomo config validation failed:\n\n${validation: -2500}" 20 90
        return
    fi
    nohup "$binary" -f "$config" -d "$home" >>"$LOG_FILE" 2>&1 </dev/null &
    pid=$!
    jq -n --argjson pid "$pid" --arg binary "$binary" --arg config_file "$config" --arg home "$home" \
        '{pid:$pid,binary:$binary,config_file:$config_file,home:$home}' >"$STATE_FILE" || {
        kill "$pid" 2>/dev/null || true
        show_message "Could not save managed-kernel state."
        return
    }
    chmod 600 "$STATE_FILE"
    sleep 0.2
    if managed_process_matches "$pid" "$binary" "$config" "$home"; then
        show_message "Mihomo started (PID $pid).\n\nLog: $LOG_FILE\n\nThe API address and secret in tui-9000 Settings must match Clash Verge Rev / Mihomo's external-controller and secret."
    else
        rm -f "$STATE_FILE"
        show_message "Mihomo did not stay running. Check the log:\n$LOG_FILE" 12 78
    fi
}

stop_kernel() {
    local pid binary config home
    managed_state || { show_message "No managed-kernel state was found."; return; }
    pid=$(jq -r '.pid' "$STATE_FILE")
    binary=$(jq -r '.binary' "$STATE_FILE")
    config=$(jq -r '.config_file' "$STATE_FILE")
    home=$(jq -r '.home' "$STATE_FILE")
    if ! managed_process_matches "$pid" "$binary" "$config" "$home"; then
        rm -f "$STATE_FILE"
        show_message "The saved managed-kernel process is no longer running."
        return
    fi
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..50}; do
        managed_process_matches "$pid" "$binary" "$config" "$home" || break
        sleep 0.1
    done
    if managed_process_matches "$pid" "$binary" "$config" "$home"; then kill -KILL "$pid" 2>/dev/null || true; fi
    rm -f "$STATE_FILE"
    show_message "Managed Mihomo kernel stopped."
}

kernel_menu() {
    local status choice
    status=$(managed_kernel_status)
    if [[ "$status" == running* ]]; then
        choice=$(choose_menu "Managed kernel: $status\nBinary: $MIHOMO_BINARY\nConfig: ${MIHOMO_CONFIG:-not set}" stop "Stop managed kernel") || return
        [[ "$choice" == stop ]] || return
        if whiptail --title "$TITLE" --yesno "Stop the Mihomo kernel started by tui-9000?" 10 72; then stop_kernel; fi
    else
        choice=$(choose_menu "Managed kernel: stopped\nBinary: $MIHOMO_BINARY\nConfig: ${MIHOMO_CONFIG:-not set}" start "Validate config and start kernel") || return
        [[ "$choice" == start ]] && start_kernel
    fi
}

settings_menu() {
    local choice value secret_label
    while choice=$(choose_menu "For Clash Verge Rev, enable Settings → External Controller first.\nSecret file mode: 0600." \
        endpoint "Controller address · $ENDPOINT" \
        secret "Bearer API secret · $(if ((SECRET_OVERRIDE_SET)); then printf 'supplied on command line'; elif [[ -n "$SECRET" ]]; then printf configured; else printf 'not set'; fi)" \
        binary "Mihomo binary · $MIHOMO_BINARY" \
        config "Mihomo config · ${MIHOMO_CONFIG:-not set}" \
        back "Back"); do
        case "$choice" in
            endpoint)
                value=$(capture_dialog --inputbox "Controller address (host:port or URL):" 10 78 "$ENDPOINT") || continue
                if [[ "$value" != *://* ]]; then value="http://$value"; fi
                if [[ "$value" != http://* && "$value" != https://* ]]; then show_message "Enter an HTTP(S) controller URL."; continue; fi
                ENDPOINT="${value%/}"
                save_settings || show_message "Could not save settings."
                ;;
            secret)
                value=$(capture_dialog --passwordbox "Controller secret / Bearer token (from Clash Verge Rev External Controller):" 10 78 "$SECRET") || continue
                SECRET="$value"
                SAVED_SECRET="$value"
                SECRET_OVERRIDE_SET=0
                save_settings || show_message "Could not save settings."
                ;;
            binary)
                value=$(capture_dialog --inputbox "Mihomo executable path or command name:" 10 78 "$MIHOMO_BINARY") || continue
                MIHOMO_BINARY="$value"
                save_settings || show_message "Could not save settings."
                ;;
            config)
                value=$(capture_dialog --inputbox "Path to Mihomo YAML config file:" 10 78 "$MIHOMO_CONFIG") || continue
                MIHOMO_CONFIG="$value"
                save_settings || show_message "Could not save settings."
                ;;
            back) return ;;
            *) show_message "Unexpected Settings selection: $choice" ;;
        esac
    done
}

main() {
    local dependency choice argument
    while (($#)); do
        argument="$1"
        case "$argument" in
            -s|--secret)
                if (($# < 2)); then printf '%s\n' 'Missing value for --secret.' >&2; return 2; fi
                SECRET_OVERRIDE="$2"
                SECRET_OVERRIDE_SET=1
                shift 2
                ;;
            -s=*|--secret=*)
                SECRET_OVERRIDE="${argument#*=}"
                SECRET_OVERRIDE_SET=1
                shift
                ;;
            --help|-h)
                printf 'Usage: %s [-s|--secret BEARER_TOKEN]\n' "${0##*/}"
                printf 'The command-line secret overrides the saved secret for this run.\n'
                return 0
                ;;
            *) printf 'Unknown argument: %s\n' "$argument" >&2; return 2 ;;
        esac
    done
    for dependency in whiptail curl jq; do
        if ! command -v "$dependency" >/dev/null 2>&1; then
            printf 'tui-9000.sh requires %s. Install it and run this script again.\n' "$dependency" >&2
            return 1
        fi
    done
    if [[ ! -t 0 || ! -t 1 ]]; then
        printf 'tui-9000.sh must be run in an interactive terminal.\n' >&2
        return 1
    fi
    load_settings
    if ((SECRET_OVERRIDE_SET)); then SECRET="$SECRET_OVERRIDE"; fi
    while choice=$(choose_menu "Controller: $ENDPOINT\n\nUse ↑/↓ and Enter to choose an action." \
        status "Test controller / connection status" \
        proxies "Proxy groups" \
        traffic "Current traffic" \
        connections "Active connections" \
        mode "Routing mode" \
        kernel "Start / stop local Mihomo" \
        settings "Connection and kernel settings" \
        exit "Exit tui-9000"); do
        case "$choice" in
            status) show_status ;;
            proxies) proxy_groups ;;
            traffic) show_traffic ;;
            connections) active_connections ;;
            mode) routing_mode ;;
            kernel) kernel_menu ;;
            settings) settings_menu ;;
            exit) return 0 ;;
            *) show_message "Unexpected menu selection: $choice" ;;
        esac
    done
    return 0
}

main "$@"
