# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional


@dataclass
class CredentialData:
    email: str = ""
    csesidx: str = ""
    config_id: str = ""
    c_ses: str = ""
    c_oses: str = ""
    mail_provider: str = ""
    mail_address: str = ""
    mail_password: str = ""
    mail_base_url: str = ""
    mail_api_key: str = ""
    mail_domain: str = ""

    def is_complete(self) -> bool:
        return all([self.email, self.csesidx, self.config_id, self.c_ses, self.c_oses])

    def to_dict(self, existing: Optional[Dict] = None) -> Dict:
        payload = dict(existing or {})
        expire_hours = int(os.environ.get("ACCOUNT_EXPIRE_HOURS", "20"))
        payload.update(
            {
                "id": self.email,
                "csesidx": self.csesidx,
                "config_id": self.config_id,
                "secure_c_ses": self.c_ses,
                "host_c_oses": self.c_oses,
                "expires_at": (datetime.now() + timedelta(hours=expire_hours)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
        for key in [
            "mail_provider",
            "mail_address",
            "mail_password",
            "mail_base_url",
            "mail_api_key",
            "mail_domain",
        ]:
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload
