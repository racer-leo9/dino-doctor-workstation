# -*- coding: utf-8 -*-
"""Feishu Bitable helper: upload files and create/update records."""
import json, os, sys, time
from pathlib import Path

import requests


class FeishuHelper:
    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id, app_secret, app_token, table_id):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = None
        self._expires = 0

    def _get_token(self):
        if self._token and time.time() < self._expires:
            return self._token
        r = requests.post(f"{self.BASE}/auth/v3/tenant_access_token/internal", json={
            "app_id": self.app_id, "app_secret": self.app_secret,
        })
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"Auth failed: {d}")
        self._token = d["tenant_access_token"]
        self._expires = time.time() + d.get("expire", 7200) - 300
        return self._token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    def upload_file(self, file_path, file_name=None):
        """Upload a file to Feishu Drive and return file_token."""
        file_path = Path(file_path)
        if not file_path.exists():
            print(f"File not found: {file_path}", file=sys.stderr)
            return None

        if file_name is None:
            file_name = file_path.name

        file_size = file_path.stat().st_size

        # Determine parent node: use root
        # For Bitable attachments, we need to upload to drive first
        url = f"{self.BASE}/drive/v1/medias/upload_all"

        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers=self._headers(),
                data={
                    "file_name": file_name,
                    "parent_type": "bitable_image",
                    "parent_node": self.app_token,
                    "size": str(file_size),
                },
                files={"file": (file_name, f)},
            )

        d = resp.json()
        if d.get("code") == 0:
            file_token = d.get("data", {}).get("file_token", "")
            print(f"  Uploaded {file_name} -> {file_token}", file=sys.stderr)
            return file_token
        else:
            print(f"  Upload failed for {file_name}: {d}", file=sys.stderr)
            return None

    def upload_image(self, file_path, file_name=None):
        """Upload an image to Feishu Drive and return file_token."""
        file_path = Path(file_path)
        if not file_path.exists():
            return None

        if file_name is None:
            file_name = file_path.name

        file_size = file_path.stat().st_size
        url = f"{self.BASE}/drive/v1/medias/upload_all"

        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers=self._headers(),
                data={
                    "file_name": file_name,
                    "parent_type": "bitable_image",
                    "parent_node": self.app_token,
                    "size": str(file_size),
                },
                files={"file": (file_name, f, "image/png")},
            )

        d = resp.json()
        if d.get("code") == 0:
            token = d.get("data", {}).get("file_token", "")
            print(f"  Uploaded image {file_name} -> {token}", file=sys.stderr)
            return token
        else:
            print(f"  Image upload failed: {d}", file=sys.stderr)
            return None

    def create_record(self, fields):
        """Create a single record in the Bitable."""
        url = f"{self.BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"
        resp = requests.post(url, headers={**self._headers(), "Content-Type": "application/json"}, json={"fields": fields})
        d = resp.json()
        if d.get("code") == 0:
            record_id = d.get("data", {}).get("record", {}).get("record_id", "")
            print(f"  Record created: {record_id}", file=sys.stderr)
            return record_id
        else:
            print(f"  Record creation failed: {d}", file=sys.stderr)
            return None

    def update_record(self, record_id, fields):
        """Update an existing record."""
        url = f"{self.BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{record_id}"
        resp = requests.put(url, headers={**self._headers(), "Content-Type": "application/json"}, json={"fields": fields})
        d = resp.json()
        if d.get("code") == 0:
            print(f"  Record updated: {record_id}", file=sys.stderr)
            return True
        else:
            print(f"  Record update failed: {d}", file=sys.stderr)
            return False

    def create_record_with_files(self, fields, video_path=None, frame_paths=None):
        """Create a record, uploading video and frames as attachments."""
        # Upload video
        if video_path:
            vt = self.upload_file(video_path)
            if vt:
                fields["原视频文件"] = [{"file_token": vt}]

        # Upload frame images
        if frame_paths:
            tokens = []
            for fp in frame_paths:
                t = self.upload_image(fp)
                if t:
                    tokens.append({"file_token": t})
            if tokens:
                fields["关键帧图片"] = tokens

        return self.create_record(fields)


if __name__ == "__main__":
    # Test: upload a file
    if len(sys.argv) < 3:
        print("Usage: python feishu_upload.py <FILE_PATH> <FILE_NAME>")
        sys.exit(1)
    # Load config
    import yaml
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    helper = FeishuHelper(
        cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"],
        cfg["feishu"]["bitable"]["app_token"], cfg["feishu"]["bitable"]["table_id"],
    )
    token = helper.upload_file(sys.argv[1], sys.argv[2])
    print(f"file_token: {token}")

