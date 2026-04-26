#!/usr/bin/env python3
"""Ejecuta todos los tests del backend.

Uso:
    python3 rtests.py              # todos los tests
    python3 rtests.py -v           # verbose
    python3 rtests.py tests/api/   # solo un subdirectorio
    python3 rtests.py -k auth      # filtrar por nombre
"""
import subprocess
import sys


def main() -> None:
    # Instalar dependencias de test si faltan
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        check=True,
    )

    args = sys.argv[1:] or ["tests", "-v", "--tb=short"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
