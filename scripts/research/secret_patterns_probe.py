#!/usr/bin/env python3
"""Count which common credential shapes the scanner catches. Values are synthetic; prints JSON."""
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.update(MEMORY_USE_CONFIG_DIR=tempfile.mkdtemp(), MEMORY_USE_STATE_DIR=tempfile.mkdtemp(),
                  MEMORY_USE_SECRETS="/nonexistent", MEMORY_USE_SECRET_HASHES="/nonexistent")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_use as m  # noqa: E402

V = "abcd1234EFGH5678ijkl9012MNOP3456qrst7890"
SECRET = {
    "url-credential": f"git clone https://leo:{V[:16]}@git.example.com/x.git",
    "postgres-url": f"postgres://admin:{V[:14]}@10.0.0.2/db",
    "bearer-header": f"curl -H 'Authorization: Bearer {V}'",
    "basic-header": "Authorization: Basic bGVvOnN1cGVyc2VjcmV0MTIz",
    "x-api-key": f"X-API-Key: {V}",
    "google": "AIzaSyA" + V[:32],
    "stripe": "sk_live_" + V[:24],
    "npm": "npm_" + V[:36],
    "github-fine": "github_pat_" + V + V[:20],
    "gitlab": "glpat-" + V[:20],
    "huggingface": "hf_" + V[:34],
    "anthropic": "sk-ant-api03-" + V,
    "slack": "xoxb-123456789012-" + V[:24],
    "telegram": "123456789:AA" + V[:33],
    "tailscale": "tskey-auth-" + V[:30],
    "env-suffix": f"CLOUDFLARE_API_TOKEN={V}",
    "export": f"export API_KEY={V}",
    "json-key": f'{{"password": "{V[:12]}"}}',
    "yaml-key": f"api_key: {V[:20]}",
    "wireguard": "PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=",
    "zh-colon": "令牌：abc123XYZ789",
    "zh-is": "密钥是 abc123XYZ789",
    "mysql-flag": f"mysql -uroot -p{V[:12]} -h db",
    "sshpass-flag": f"sshpass -p {V[:12]} ssh root@nas",
    "numeric-password": "password: 12345678",
    "numeric-zh": "密码：66668888",
}
BENIGN = {
    "token-limit": "MAX_TOKEN=4096", "limit-name": "TOKEN_LIMIT=8192", "url-port": "https://example.com:8443/path@v2",
    "url-placeholder": "postgres://admin:${DB_PASS}@db/app", "url-word": "https://user:password@host/",
    "bearer-var": "Authorization: Bearer $TOKEN", "pointer": "password: bw:0a1b2c3d", "json-word": '{"password": "required"}',
    "mysql-port": "mysql --port 3306 -h db", "mysql-prompt": "mysql -uroot -p", "path": "API_KEY=~/secrets.env",
}
caught = sorted(k for k, v in SECRET.items() if m.scan_text(v, [], set()))
flagged = sorted(k for k, v in BENIGN.items() if m.scan_text(v, [], set()))
print(json.dumps({"secret_cases": len(SECRET), "caught": len(caught), "missed": sorted(set(SECRET) - set(caught)),
                  "benign_cases": len(BENIGN), "false_positives": flagged}, indent=2))
