"""
build_installer.py — Build Windows installer for BehEDR Agent

Creates a standalone executable and MSI installer:
1. Bundles agent.py + Python runtime → agent.exe (PyInstaller)
2. Creates MSI installer (cx_Freeze or manual NSIS)
3. Installer registers as Windows Service

Requirements:
    pip install pyinstaller pywin32

Usage:
    python build_installer.py

Output:
    dist/BehEDR-Agent-Setup.exe (installer)
    dist/agent.exe (standalone agent)
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

VERSION = "4.0.0"
COMPANY = "BehEDR Project"
PRODUCT_NAME = "BehEDR Security Agent"


def clean_build():
    """Remove old build artifacts"""
    print("[1/5] Cleaning old build artifacts...")
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            shutil.rmtree(d)
    DIST_DIR.mkdir(exist_ok=True)
    print("✓ Cleaned")


def build_agent_exe():
    """Build agent.exe using PyInstaller"""
    print("\n[2/5] Building agent.exe with PyInstaller...")
    
    # PyInstaller spec
    cmd = [
        "pyinstaller",
        "--onefile",                    # Single executable
        "--noconsole",                  # No console window
        "--name", "behedr-agent",
        "--icon", "NONE",               # Add icon if you have one
        "--add-data", "agent.py;.",     # Include agent.py
        "--hidden-import", "win32timezone",
        "--hidden-import", "psutil",
        "--hidden-import", "requests",
        "agent.py"
    ]
    
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    
    if result.returncode != 0:
        print("✗ PyInstaller failed")
        sys.exit(1)
    
    print("✓ agent.exe built successfully")


def create_install_script():
    """Create installer script (NSIS or batch)"""
    print("\n[3/5] Creating installer script...")
    
    # Simple batch installer for now (you can replace with NSIS later)
    batch_script = f'''@echo off
echo ================================================
echo   BehEDR Security Agent v{VERSION}
echo   Installation
echo ================================================
echo.

REM Check admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Administrator rights required!
    echo Right-click this file and select "Run as administrator"
    pause
    exit /b 1
)

echo [1/4] Copying agent files...
if not exist "%ProgramFiles%\\BehEDR" mkdir "%ProgramFiles%\\BehEDR"
copy /Y behedr-agent.exe "%ProgramFiles%\\BehEDR\\behedr-agent.exe"

echo [2/4] Configuring server connection...
set /p SERVER_IP="Enter EDR server IP address (or press Enter for 10.255.74.71): "
if "%SERVER_IP%"=="" set SERVER_IP=10.255.74.71

setx EDR_SERVER_IP "%SERVER_IP%" /M
setx EDR_API_KEY "123456789" /M

echo [3/4] Installing Windows Service...
sc create BehEDRAgent binPath= "\\""%ProgramFiles%\\BehEDR\\behedr-agent.exe\\"" --service" start= auto DisplayName= "BehEDR Security Agent" depend= Tcpip

echo [4/4] Starting service...
sc start BehEDRAgent

echo.
echo ================================================
echo   Installation Complete!
echo ================================================
echo   Server IP: %SERVER_IP%
echo   Service Status: sc query BehEDRAgent
echo   View Logs: %ProgramFiles%\\BehEDR\\agent.log
echo.
echo Press any key to exit...
pause >nul
'''
    
    install_bat = DIST_DIR / "install.bat"
    install_bat.write_text(batch_script)
    print("✓ Installer script created")


def create_uninstall_script():
    """Create uninstaller"""
    print("\n[4/5] Creating uninstaller...")
    
    uninstall_script = '''@echo off
echo ================================================
echo   BehEDR Security Agent
echo   Uninstallation
echo ================================================
echo.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Administrator rights required!
    pause
    exit /b 1
)

echo [1/3] Stopping service...
sc stop BehEDRAgent

echo [2/3] Removing service...
sc delete BehEDRAgent

echo [3/3] Removing files...
rmdir /S /Q "%ProgramFiles%\\BehEDR"

echo.
echo Uninstallation complete!
pause
'''
    
    uninstall_bat = DIST_DIR / "uninstall.bat"
    uninstall_bat.write_text(uninstall_script)
    print("✓ Uninstaller created")


def create_readme():
    """Create README for installer package"""
    print("\n[5/5] Creating documentation...")
    
    readme = f'''# BehEDR Security Agent v{VERSION}

## Installation

1. Right-click `install.bat` and select "Run as administrator"
2. Enter your EDR server IP address when prompted
3. Wait for installation to complete

## Verification

Check service status:
```
sc query BehEDRAgent
```

View logs:
```
notepad "%ProgramFiles%\\BehEDR\\agent.log"
```

## Uninstallation

Right-click `uninstall.bat` and select "Run as administrator"

## Manual Service Control

Start:  `sc start BehEDRAgent`
Stop:   `sc stop BehEDRAgent`
Restart: `sc stop BehEDRAgent && sc start BehEDRAgent`

## Configuration

Environment variables (set before service start):
- EDR_SERVER_IP: Server IP address (default: 10.255.74.71)
- EDR_PORT: Server port (default: 5000)
- EDR_API_KEY: API key (default: 123456789)
- EDR_TLS_ENABLED: Enable HTTPS (default: false)

## Support

Documentation: https://github.com/yourproject/behedr
Issues: https://github.com/yourproject/behedr/issues
'''
    
    readme_file = DIST_DIR / "README.txt"
    readme_file.write_text(readme)
    print("✓ Documentation created")


def package_installer():
    """Package everything into a distributable folder"""
    print("\n[FINAL] Packaging installer...")
    
    # Copy agent executable
    agent_exe = BUILD_DIR / "dist" / "behedr-agent.exe"
    if agent_exe.exists():
        shutil.copy(agent_exe, DIST_DIR / "behedr-agent.exe")
    
    print("\n" + "=" * 60)
    print("  BUILD COMPLETE!")
    print("=" * 60)
    print(f"\nInstaller package: {DIST_DIR.absolute()}")
    print("\nContents:")
    print("  • behedr-agent.exe  - Standalone agent")
    print("  • install.bat       - Installer (run as admin)")
    print("  • uninstall.bat     - Uninstaller")
    print("  • README.txt        - Documentation")
    print("\nNext steps:")
    print("  1. Test install.bat on a clean Windows machine")
    print("  2. Verify service starts: sc query BehEDRAgent")
    print("  3. Check server dashboard for agent connection")
    print("\n" + "=" * 60)


def main():
    """Main build process"""
    print("=" * 60)
    print(f"  BehEDR Agent Installer Builder v{VERSION}")
    print("=" * 60)
    
    try:
        clean_build()
        build_agent_exe()
        create_install_script()
        create_uninstall_script()
        create_readme()
        package_installer()
        
    except KeyboardInterrupt:
        print("\n\nBuild cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Check dependencies
    try:
        import PyInstaller
    except ImportError:
        print("ERROR: PyInstaller not installed")
        print("Run: pip install pyinstaller pywin32")
        sys.exit(1)
    
    main()
