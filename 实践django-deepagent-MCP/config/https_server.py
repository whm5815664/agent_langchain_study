# -*- coding: utf-8 -*-
"""HTTPS ASGI 开发服务器：自签证书 + CSRF 信任源 + WebSocket (WSS)。"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


def _iter_local_ipv4() -> list[str]:
    ips: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    extra = os.environ.get("EXTRA_HOST_IPS", "").strip()
    if extra:
        for item in extra.split(","):
            ip = item.strip()
            if ip:
                ips.add(ip)

    return sorted(ips)


def write_openssl_config(cert_dir: Path, extra_ips: list[str] | None = None) -> Path:
    """写入/更新 openssl 配置，SAN 包含本机网卡 IP，便于 IP 访问。"""
    config_file = cert_dir / "openssl.cnf"
    ips = ["127.0.0.1"]
    for ip in extra_ips or []:
        if ip not in ips:
            ips.append(ip)

    alt_lines = ["DNS.1 = localhost"]
    for i, ip in enumerate(ips, start=1):
        alt_lines.append(f"IP.{i} = {ip}")

    config_file.write_text(
        "\n".join(
            [
                "[ req ]",
                "distinguished_name = req_distinguished_name",
                "x509_extensions = v3_req",
                "prompt = no",
                "[ req_distinguished_name ]",
                "CN = localhost",
                "[ v3_req ]",
                "subjectAltName = @alt_names",
                "[ alt_names ]",
                *alt_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_file


def ensure_certificate(cert_dir: Path) -> tuple[Path, Path]:
    cert_file = cert_dir / "localhost.crt"
    key_file = cert_dir / "localhost.key"
    local_ips = _iter_local_ipv4()
    cert_dir.mkdir(parents=True, exist_ok=True)
    config_file = write_openssl_config(cert_dir, local_ips)

    # 已有证书则复用；若要用新 SAN（含局域网/公网 IP），请删除 certs 后重启
    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-days",
        "3650",
        "-nodes",
        "-subj",
        "/CN=localhost",
        "-config",
        str(config_file),
        "-extensions",
        "v3_req",
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 openssl，无法生成 HTTPS 证书。请安装 OpenSSL 或 Git for Windows。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"生成 HTTPS 证书失败：{detail}") from exc

    if not cert_file.exists() or not key_file.exists():
        raise RuntimeError("HTTPS 证书生成后文件不存在。")

    return cert_file, key_file


def build_csrf_trusted_origins(port: int) -> list[str]:
    origins = {
        f"https://127.0.0.1:{port}",
        f"https://localhost:{port}",
        f"https://[::1]:{port}",
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }
    for ip in _iter_local_ipv4():
        origins.add(f"https://{ip}:{port}")
        origins.add(f"http://{ip}:{port}")

    preset = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
    if preset:
        for item in preset.split(","):
            value = item.strip()
            if value:
                origins.add(value)

    return sorted(origins)


def main() -> None:
    parser = argparse.ArgumentParser(description="Django HTTPS ASGI development server (WSS)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--directory", required=True, help="Django project root")
    parser.add_argument("--cert-dir", required=True)
    args = parser.parse_args()

    project_root = Path(args.directory).resolve()
    cert_dir = Path(args.cert_dir).resolve()
    cert_file, key_file = ensure_certificate(cert_dir)

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    trusted = build_csrf_trusted_origins(args.port)
    os.environ["DJANGO_CSRF_TRUSTED_ORIGINS"] = ",".join(trusted)
    print("CSRF trusted origins:", flush=True)
    for origin in trusted:
        print(f"  - {origin}", flush=True)
    lan_ips = _iter_local_ipv4()
    if lan_ips:
        print("Detected host IPs: " + ", ".join(lan_ips), flush=True)
        print(
            "Remote IP access: CSRF trusts browser Origin from detected host IPs. "
            "Optional EXTRA_HOST_IPS only affects certificate SAN; delete certs/ to regenerate.",
            flush=True,
        )

    import uvicorn

    print(f"Serving HTTPS/WSS on https://{args.bind}:{args.port}", flush=True)
    print(f"Local URL: https://127.0.0.1:{args.port}", flush=True)
    for ip in lan_ips:
        print(f"LAN/Host URL: https://{ip}:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    print("MCP: run mcp/startmcpserver/start_mcp.bat (or python -m mcp.startmcpserver) to start local bridge.", flush=True)

    uvicorn.run(
        "config.asgi:application",
        host=args.bind,
        port=args.port,
        ssl_certfile=str(cert_file),
        ssl_keyfile=str(key_file),
        reload=False,
        log_level="info",
        ws="websockets",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as error:
        print(error, file=sys.stderr)
        sys.exit(1)
