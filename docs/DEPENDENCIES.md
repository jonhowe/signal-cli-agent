# Dependencies and Development Setup

This document explains the dependency management approach for Signal CLI Agent.

## Dependency Management

Signal CLI Agent uses modern Python packaging standards with backward compatibility:

### Modern Python Packaging (Recommended)
```bash
# Install in development mode with all dependencies
pip install -e .[dev]

# Install only runtime dependencies
pip install -e .

# Install specific optional dependencies
pip install -e .[security]  # Security analysis tools
pip install -e .[docs]      # Documentation building
pip install -e .[all]       # Everything
```

### Legacy/Simple Setup (Backward Compatible)
```bash
# Runtime dependencies only
pip install -r requirements.txt

# Development dependencies
pip install -r requirements-dev.txt
```

## Core Dependencies

### Runtime Dependencies
- **PyYAML (>=6.0)** - YAML configuration parsing
- **pydbus (>=0.6.0)** - DBus communication with signal-cli
- **PyGObject (>=3.40.0)** - GLib event loop and DBus integration

### System Dependencies (Container/Host)
- **signal-cli** - Signal protocol implementation
- **DBus** - Inter-process communication
- **Java 17+** - Required by signal-cli
- **Python 3.9+** - Runtime environment

## Development Tools

### Code Quality
- **pytest** - Testing framework
- **black** - Code formatting
- **isort** - Import sorting
- **mypy** - Type checking
- **flake8** - Linting

### Security Analysis
- **bandit** - Python security analysis
- **safety** - Dependency vulnerability checking

## Container Dependencies

The Docker container installs system packages for core dependencies:
```dockerfile
RUN apt-get install -y \
    python3 python3-pip \
    python3-yaml python3-gi gir1.2-glib-2.0 python3-pydbus \
    dbus dbus-user-session \
    openjdk-17-jre-headless
```

Then installs Python packages from `pyproject.toml`:
```dockerfile
RUN pip3 install /app/
```

## Why These Choices?

### Security-First Approach
- **Minimal dependencies** - Uses stdlib `urllib` instead of `requests`
- **System packages** for DBus/GLib integration (more stable)
- **Pinned versions** with security-focused ranges

### Container Optimization
- **Multi-layer approach** - System deps + Python deps
- **Cached layers** - System packages change less frequently
- **Small attack surface** - Minimal runtime dependencies

## Development Workflow

### Local Development
```bash
# Set up development environment
git clone <repo>
cd signal-cli-agent
pip install -e .[dev]

# Run tests
pytest

# Format code
black .
isort .

# Type checking
mypy .

# Security analysis
bandit -r .
safety check
```

### Container Development
```bash
# Build with dependency cache
docker build -f docker/Dockerfile .

# Or use provided Make targets
make docker-up-build
```

## Troubleshooting

### Missing System Dependencies
If you see DBus/GLib import errors:
```bash
# Ubuntu/Debian
sudo apt install python3-gi gir1.2-glib-2.0 python3-pydbus

# Fedora/RHEL
sudo dnf install python3-gobject python3-pydbus

# Arch
sudo pacman -S python-gobject python-pydbus
```

### Development Dependencies
For missing development tools:
```bash
pip install -e .[dev]
# or
pip install -r requirements-dev.txt
```

## Migration from Legacy Setup

If you were using manual dependency installation:
1. Remove old virtual environments
2. Use `pip install -e .[dev]` for development
3. Update CI/CD to use `pyproject.toml`
4. Container builds will use the new dependencies automatically