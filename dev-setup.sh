#!/bin/bash
# RadPro HA Development Setup Script
# Quickly deploys Home Assistant with the component mounted directly

set -e

# Configuration
CONTAINER_NAME="homeassistant-radpro-dev"
HA_CONFIG_DIR="${HA_CONFIG_DIR:-$HOME/ha-radpro-dev/config}"
COMPONENT_DIR="$(cd "$(dirname "$0")" && pwd)/custom_components/radpro"
SERIAL_PORT="${SERIAL_PORT:-auto}"
TZ="${TZ:-UTC}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_help() {
    cat << EOF
RadPro HA Development Setup

Usage: $0 [command] [options]

Commands:
  start       Start/create the HA container (default)
  stop        Stop the container
  restart     Restart the container (use after code changes)
  logs        Show container logs (follow mode)
  shell       Open shell in the container
  clean       Stop and remove container + config
  status      Show container status
  native      Run HA Core natively (macOS - for serial port access)

Options:
  --port PORT     Serial port for RadPro device (default: auto)
  --config DIR    HA config directory (default: ~/ha-radpro-dev/config)
  --tz TIMEZONE   Timezone (default: UTC)
  -h, --help      Show this help

Environment variables:
  SERIAL_PORT     Same as --port
  HA_CONFIG_DIR   Same as --config
  TZ              Same as --tz

Examples:
  $0 start                          # Start with auto-detect (Docker)
  $0 native --port /dev/cu.usbmodem14101  # Run natively on macOS
  $0 restart                        # Restart after code changes
  $0 logs                           # View logs
  SERIAL_PORT=/dev/ttyUSB0 $0 start # Using env var

Note: On macOS, Docker cannot access serial ports directly.
      Use '$0 native' for development with real hardware.

EOF
}

parse_args() {
    COMMAND="${1:-start}"
    shift || true

    while [[ $# -gt 0 ]]; do
        case $1 in
            --port)
                SERIAL_PORT="$2"
                shift 2
                ;;
            --config)
                HA_CONFIG_DIR="$2"
                shift 2
                ;;
            --tz)
                TZ="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi

    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running or you don't have permissions."
        exit 1
    fi
}

check_component() {
    if [[ ! -d "$COMPONENT_DIR" ]]; then
        log_error "Component directory not found: $COMPONENT_DIR"
        log_error "Run this script from the radpro-ha repository root."
        exit 1
    fi
    log_info "Component directory: $COMPONENT_DIR"
}

create_config() {
    mkdir -p "$HA_CONFIG_DIR"

    if [[ ! -f "$HA_CONFIG_DIR/configuration.yaml" ]]; then
        log_info "Creating configuration.yaml..."
        cat > "$HA_CONFIG_DIR/configuration.yaml" << EOF
# Home Assistant Configuration for RadPro Development

default_config:

logger:
  default: info
  logs:
    custom_components.radpro: debug

radpro:
  port: ${SERIAL_PORT}
  scan_interval: 2
EOF
        log_info "Created: $HA_CONFIG_DIR/configuration.yaml"
    else
        log_info "Using existing configuration.yaml"
    fi
}

container_exists() {
    docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

container_running() {
    docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

cmd_start() {
    check_docker
    check_component
    create_config

    if container_running; then
        log_warn "Container '$CONTAINER_NAME' is already running"
        log_info "Use '$0 restart' to restart or '$0 logs' to view logs"
        return
    fi

    if container_exists; then
        log_info "Starting existing container..."
        docker start "$CONTAINER_NAME"
    else
        log_info "Creating and starting container..."
        # Note: Using -p 8123:8123 instead of --network=host for macOS compatibility
        docker run -d \
            --name "$CONTAINER_NAME" \
            --privileged \
            --restart=unless-stopped \
            -e TZ="$TZ" \
            -p 8123:8123 \
            -v "$HA_CONFIG_DIR:/config" \
            -v "$COMPONENT_DIR:/config/custom_components/radpro:ro" \
            -v /dev:/dev \
            ghcr.io/home-assistant/home-assistant:stable
    fi

    log_info "Container started!"
    log_info ""
    log_info "Home Assistant will be available at: http://localhost:8123"
    log_info "Component mounted from: $COMPONENT_DIR"
    log_info ""
    log_info "Commands:"
    log_info "  $0 logs      - View logs"
    log_info "  $0 restart   - Restart after code changes"
    log_info "  $0 stop      - Stop container"
}

cmd_stop() {
    if container_running; then
        log_info "Stopping container..."
        docker stop "$CONTAINER_NAME"
        log_info "Container stopped"
    else
        log_warn "Container is not running"
    fi
}

cmd_restart() {
    if container_exists; then
        log_info "Restarting container..."
        docker restart "$CONTAINER_NAME"
        log_info "Container restarted"
        log_info "View logs: $0 logs"
    else
        log_warn "Container does not exist. Use '$0 start' first."
    fi
}

cmd_logs() {
    if container_exists; then
        log_info "Following logs (Ctrl+C to exit)..."
        docker logs -f "$CONTAINER_NAME"
    else
        log_error "Container does not exist"
    fi
}

cmd_shell() {
    if container_running; then
        docker exec -it "$CONTAINER_NAME" /bin/bash
    else
        log_error "Container is not running"
    fi
}

cmd_clean() {
    if container_exists; then
        log_info "Stopping and removing container..."
        docker rm -f "$CONTAINER_NAME"
    fi

    if [[ -d "$HA_CONFIG_DIR" ]]; then
        read -p "Remove config directory $HA_CONFIG_DIR? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$HA_CONFIG_DIR"
            log_info "Config directory removed"
        fi
    fi

    log_info "Cleanup complete"
}

cmd_status() {
    if container_running; then
        echo -e "Container: ${GREEN}running${NC}"
        docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Status}}\t{{.Ports}}"
    elif container_exists; then
        echo -e "Container: ${YELLOW}stopped${NC}"
    else
        echo -e "Container: ${RED}not created${NC}"
    fi
    echo ""
    echo "Config dir: $HA_CONFIG_DIR"
    echo "Component:  $COMPONENT_DIR"
}

cmd_native() {
    # Run Home Assistant Core natively on macOS for serial port access
    NATIVE_VENV="${HOME}/ha-radpro-venv"
    NATIVE_CONFIG="${HOME}/ha-radpro-native"

    log_info "Setting up native Home Assistant Core..."
    log_info "This allows direct access to serial ports on macOS"
    echo ""
  
    PYTHON_BIN=""
    if command -v python3.14 &> /dev/null; then
        PYTHON_BIN="python3.14"
    elif command -v python3.13 &> /dev/null; then
        PYTHON_BIN="python3.13"
    elif command -v python3 &> /dev/null; then
        PYTHON_BIN="python3"
        log_warn "Using default python3. For best results, install Python 3.14: brew install python@3.14"
    else
        log_error "Python 3 is required. Install with: brew install python@3.14"
    fi

    log_info "Using Python: $($PYTHON_BIN --version)"

    # Create venv if not exists
    if [[ ! -d "$NATIVE_VENV" ]]; then
        log_info "Creating virtual environment at $NATIVE_VENV..."
        $PYTHON_BIN -m venv "$NATIVE_VENV"
    fi

    # Activate venv
    source "$NATIVE_VENV/bin/activate"

    # Install/upgrade dependencies
    log_info "Installing Home Assistant Core and dependencies..."
    pip install --upgrade pip > /dev/null
    pip install homeassistant pyserial > /dev/null 2>&1

    # Create config directory
    mkdir -p "$NATIVE_CONFIG/custom_components"

    # Create symlink to component
    if [[ ! -L "$NATIVE_CONFIG/custom_components/radpro" ]]; then
        ln -sf "$COMPONENT_DIR" "$NATIVE_CONFIG/custom_components/radpro"
        log_info "Linked component: $COMPONENT_DIR"
    fi

    # Create configuration.yaml if not exists
    if [[ ! -f "$NATIVE_CONFIG/configuration.yaml" ]]; then
        log_info "Creating configuration.yaml..."
        cat > "$NATIVE_CONFIG/configuration.yaml" << EOFCONF
# Home Assistant Configuration for RadPro Development (Native)

default_config:

logger:
  default: info
  logs:
    custom_components.radpro: debug

radpro:
  port: ${SERIAL_PORT}
  scan_interval: 2
EOFCONF
    fi

    # List available serial ports
    echo ""
    log_info "Available serial ports:"
    ls /dev/cu.* 2>/dev/null | grep -E "usb|modem|serial" || echo "  (none found)"
    echo ""

    if [[ "$SERIAL_PORT" == "auto" ]]; then
        log_warn "Serial port set to 'auto'. If device not found, specify with --port"
    else
        log_info "Using serial port: $SERIAL_PORT"
    fi

    echo ""
    log_info "Starting Home Assistant Core..."
    log_info "Web UI will be at: http://localhost:8123"
    log_info "Press Ctrl+C to stop"
    echo ""

    # Run Home Assistant
    hass -c "$NATIVE_CONFIG"
}

# Main
parse_args "$@"

case $COMMAND in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    logs)    cmd_logs ;;
    shell)   cmd_shell ;;
    clean)   cmd_clean ;;
    status)  cmd_status ;;
    native)  cmd_native ;;
    *)
        log_error "Unknown command: $COMMAND"
        show_help
        exit 1
        ;;
esac
