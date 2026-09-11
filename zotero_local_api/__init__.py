#!/usr/bin/env python3

"""
Zotero Local API client for Zotero 10+.

Initial version for the Zim-Zotero project.

Zotero:
    http://127.0.0.1:23119/api/

The module deliberately contains no Zim-specific code.
"""


from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

import requests


class ZoteroAPIError(Exception):
    """Base exception for Zotero Local API errors."""


class ZoteroHTTPError(ZoteroAPIError):
    """HTTP error returned by Zotero."""

    def __init__(
        self,
        status_code: int,
        message: str,
        response: Optional[requests.Response] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ZoteroLocalAPI:
    """
    Client for Zotero 10 Local API.

    The client automatically obtains Zotero-Server-ID and can
    authorize write operations through Zotero's local authorization
    dialog.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:23119/api",
        user_id: int = 0,
        app_name: str = "Zim-Zotero",
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.app_name = app_name
        self.timeout = timeout

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": "zim-zotero/0.1",
            "Zotero-API-Version": "3",
        })

        self.server_id: Optional[str] = None
        self.api_key: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, write: bool = False) -> dict[str, str]:
        headers = {}

        if self.server_id:
            headers["Zotero-Server-ID"] = self.server_id

        if write and self.api_key:
            headers["Zotero-API-Key"] = self.api_key

        return headers

    def _raise_for_error(
        self,
        response: requests.Response,
        operation: str = "Zotero request",
    ) -> None:

        if response.ok:
            return

        try:
            body = response.json()
        except Exception:
            body = response.text

        message = (
            f"{operation} failed: "
            f"HTTP {response.status_code}: {body}"
        )

        raise ZoteroHTTPError(
            response.status_code,
            message,
            response,
        )

    # ------------------------------------------------------------------
    # Connection / authorization
    # ------------------------------------------------------------------

    def connect(self) -> str:
        """
        Connect to Zotero and obtain the current Server ID.

        Returns:
            Server ID.
        """

        response = self.session.get(
            self._url("/"),
            timeout=self.timeout,
        )

        self._raise_for_error(
            response,
            "Connecting to Zotero",
        )

        server_id = response.headers.get(
            "Zotero-Server-ID"
        )

        if not server_id:
            raise ZoteroAPIError(
                "Zotero response does not contain "
                "Zotero-Server-ID"
            )

        self.server_id = server_id

        return server_id

    def authorize(self) -> bool:
        """
        Ask Zotero for permission to perform write operations.

        Zotero 10 displays the authorization dialog.

        Returns:
            True if authorization succeeded.
        """

        if not self.server_id:
            self.connect()

        response = self.session.post(
            self._url("/local/authorize"),
            json={
                "appName": self.app_name,
            },
            headers=self._headers(write=True),
            timeout=self.timeout,
        )

        self._raise_for_error(
            response,
            "Authorizing Zotero write access",
        )

        data = response.json()

        key = data.get("key")

        if not key:
            raise ZoteroAPIError(
                "Zotero authorization succeeded "
                "but no API key was returned"
            )

        self.api_key = key

        return True

    def ensure_write_access(self) -> None:
        """
        Make sure we have a valid local write key.
        """

        if not self.server_id:
            self.connect()

        if not self.api_key:
            self.authorize()

    # ------------------------------------------------------------------
    # Generic HTTP
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> requests.Response:

        response = self.session.get(
            self._url(path),
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )

        self._raise_for_error(response, "GET")

        return response

    def post_json(
        self,
        path: str,
        data: Any,
    ) -> requests.Response:

        self.ensure_write_access()

        response = self.session.post(
            self._url(path),
            json=data,
            headers=self._headers(write=True),
            timeout=self.timeout,
        )

        self._raise_for_error(response, "POST JSON")

        return response

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def get_item(
        self,
        item_key: str,
        include: Optional[str] = None,
    ) -> dict[str, Any]:

        params = {}

        if include:
            params["include"] = include

        response = self.get(
            f"/users/{self.user_id}/items/{item_key}",
            params=params,
        )

        return response.json()

    def get_item_data(
        self,
        item_key: str,
    ) -> dict[str, Any]:

        return self.get_item(item_key)["data"]

    def get_children(
        self,
        item_key: str,
    ) -> list[dict[str, Any]]:

        response = self.get(
            f"/users/{self.user_id}/items/{item_key}/children"
        )

        return response.json()

    def search(
        self,
        query: str,
        limit: Optional[int] = None,
        start: Optional[int] = None,
    ) -> list[dict[str, Any]]:

        params: dict[str, Any] = {
            "q": query,
        }

        if limit is not None:
            params["limit"] = limit

        if start is not None:
            params["start"] = start

        response = self.get(
            f"/users/{self.user_id}/items",
            params=params,
        )

        return response.json()

    def create_item(
        self,
        item_data: dict[str, Any],
    ) -> dict[str, Any]:

        response = self.post_json(
            f"/users/{self.user_id}/items",
            [item_data],
        )

        result = response.json()

        successful = result.get("successful", {})

        if "0" not in successful:
            raise ZoteroAPIError(
                f"Zotero did not create item: {result}"
            )

        return successful["0"]

    def delete_item(
        self,
        item_key: str,
        version: Optional[int] = None,
    ) -> None:

        self.ensure_write_access()

        headers = self._headers(write=True)

        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(
                version
            )

        response = self.session.delete(
            self._url(
                f"/users/{self.user_id}/items/{item_key}"
            ),
            headers=headers,
            timeout=self.timeout,
        )

        self._raise_for_error(
            response,
            "Deleting item",
        )

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def create_attachment(
        self,
        parent_item: str,
        title: str,
        filename: str,
        content_type: str,
        link_mode: str = "imported_file",
    ) -> dict[str, Any]:

        item_data = {
            "itemType": "attachment",
            "parentItem": parent_item,
            "linkMode": link_mode,
            "title": title,
            "contentType": content_type,
            "charset": "",
            "filename": filename,
            "md5": None,
            "mtime": None,
            "tags": [],
            "relations": {},
        }

        return self.create_item(item_data)

    @staticmethod
    def file_md5(
        file_path: str | os.PathLike[str],
    ) -> str:

        digest = hashlib.md5()

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)

        return digest.hexdigest()

    def upload_attachment(
        self,
        attachment_key: str,
        file_path: str | os.PathLike[str],
        content_type: Optional[str] = None,
    ) -> None:
        """
        Upload and register a complete file for an existing
        imported_file/imported_url attachment.

        This implements Zotero's three-stage full upload flow:

        1. request upload authorization
        2. upload bytes
        3. register upload
        """

        path = Path(file_path)

        if not path.is_file():
            raise ZoteroAPIError(
                f"File not found: {path}"
            )

        file_size = path.stat().st_size
        file_md5 = self.file_md5(path)
        filename = path.name
        mtime = int(path.stat().st_mtime * 1000)

        if not content_type:
            content_type = (
                "application/octet-stream"
            )

        self.ensure_write_access()

        file_url = self._url(
            f"/users/{self.user_id}/items/"
            f"{attachment_key}/file"
        )

        # --------------------------------------------------------------
        # Stage 1: request upload authorization
        # --------------------------------------------------------------

        form_data = {
            "md5": file_md5,
            "filename": filename,
            "filesize": str(file_size),
            "mtime": str(mtime),
        }

        headers = self._headers(write=True)

        headers["Content-Type"] = (
            "application/x-www-form-urlencoded"
        )

        headers["If-None-Match"] = "*"

        response = self.session.post(
            file_url,
            data=form_data,
            headers=headers,
            timeout=self.timeout,
        )

        self._raise_for_error(
            response,
            "Requesting attachment upload",
        )

        upload_info = response.json()

        if upload_info.get("exists") == 1:
            return

        upload_url = upload_info.get("url")
        upload_key = upload_info.get("uploadKey")

        if not upload_url or not upload_key:
            raise ZoteroAPIError(
                "Zotero did not return upload URL/key: "
                f"{upload_info}"
            )

        upload_content_type = (
            upload_info.get("contentType")
            or content_type
        )

        # --------------------------------------------------------------
        # Stage 2: upload bytes
        # --------------------------------------------------------------

        with open(path, "rb") as f:
            file_bytes = f.read()

        response = requests.post(
            upload_url,
            data=file_bytes,
            headers={
                "Content-Type": upload_content_type,
            },
            timeout=max(self.timeout, 60),
        )

        self._raise_for_error(
            response,
            "Uploading attachment bytes",
        )

        if response.status_code != 201:
            raise ZoteroHTTPError(
                response.status_code,
                "Unexpected upload response",
                response,
            )

        # --------------------------------------------------------------
        # Stage 3: register upload
        # --------------------------------------------------------------

        response = self.session.post(
            file_url,
            data={
                "upload": upload_key,
            },
            headers={
                **self._headers(write=True),
                "Content-Type": (
                    "application/x-www-form-urlencoded"
                ),
                "If-None-Match": "*",
            },
            timeout=self.timeout,
        )

        self._raise_for_error(
            response,
            "Registering attachment upload",
        )

        if response.status_code != 204:
            raise ZoteroHTTPError(
                response.status_code,
                "Unexpected registration response",
                response,
            )

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def find_children_by_type(
        self,
        parent_item: str,
        item_type: str,
    ) -> list[dict[str, Any]]:

        children = self.get_children(parent_item)

        return [
            item
            for item in children
            if item.get("data", {}).get("itemType")
            == item_type
        ]

    def find_thumbnail(
        self,
        parent_item: str,
    ) -> Optional[dict[str, Any]]:

        children = self.get_children(parent_item)

        for child in children:
            data = child.get("data", {})

            if data.get("itemType") != "attachment":
                continue

            content_type = (
                data.get("contentType") or ""
            )

            filename = (
                data.get("filename") or ""
            ).lower()

            title = (
                data.get("title") or ""
            ).lower()

            if (
                content_type.startswith("image/")
                and (
                    "thumbnail" in title
                    or "thumbnail" in filename
                )
            ):
                return child

        return None
