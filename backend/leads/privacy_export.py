"""Hermetic, encrypted CRM export builder for verified LGPD requests."""

import hashlib
import json
import os
import stat
import uuid
from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from leads.models import DataSubjectRequest, DataSubjectRequestEvent, Lead
from leads.privacy_inventory import (
    ACCOUNT_EXPORT_FIELDS,
    ACTIVITY_EXPORT_FIELDS,
    ATTACHMENT_METADATA_FIELDS,
    ATTRIBUTION_EXPORT_FIELDS,
    CONTACT_EXPORT_FIELDS,
    CONVERSION_EXPORT_FIELDS,
    INVENTORY_VERSION,
    LEAD_EXPORT_FIELDS,
    OPPORTUNITY_EXPORT_FIELDS,
    TASK_EXPORT_FIELDS,
    serialize_model_fields,
)

MAGIC = b"NXT-LGPD-1\0"
MAX_PLAINTEXT_BYTES = 1024 * 1024
MAX_TTL = timedelta(hours=24)


class PrivacyExportError(ValueError):
    pass


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise PrivacyExportError("Export root cannot be a symlink.")
    path = path.resolve(strict=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PrivacyExportError("Export root must be an owned directory.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PrivacyExportError(
            "Export root must not be accessible by group or others."
        )
    return path


def _lead_payload(lead: Lead) -> dict:
    payload = serialize_model_fields(lead, LEAD_EXPORT_FIELDS)
    payload["id"] = str(payload["id"])
    return payload


def prepare_encrypted_lead_export(
    *,
    request_id,
    lead_id,
    org,
    actor,
    encryption_key: bytes,
    export_root,
    now=None,
    ttl=timedelta(hours=1),
):
    """Create one encrypted artifact; no key, plaintext or raw path is persisted."""

    if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
        raise PrivacyExportError(
            "A caller-supplied 256-bit encryption key is required."
        )
    if ttl <= timedelta(0) or ttl > MAX_TTL:
        raise PrivacyExportError(
            "Export TTL must be greater than zero and at most 24 hours."
        )
    root = _private_directory(Path(export_root))
    now = now or timezone.now()
    expires_at = now + ttl
    artifact_ref = f"lgpd_{uuid.uuid4().hex}"
    output_path = root / f"{artifact_ref}.enc"
    created = False

    try:
        with transaction.atomic():
            privacy_request = (
                DataSubjectRequest.objects.select_for_update()
                .filter(pk=request_id, org=org)
                .first()
            )
            if privacy_request is None:
                raise PrivacyExportError("Privacy request not found.")
            if privacy_request.status != "verified" or privacy_request.legal_hold:
                raise PrivacyExportError(
                    "Request must be verified and free of legal hold."
                )
            if privacy_request.request_type not in {"access", "correction"}:
                raise PrivacyExportError(
                    "Deletion requests cannot create an export in this gate."
                )
            lead = Lead.objects.filter(pk=lead_id, org=org).first()
            if lead is None:
                raise PrivacyExportError("Lead not found.")
            expected_subject = hashlib.sha256(
                f"{org.id}:{lead.id}".encode()
            ).hexdigest()
            if privacy_request.subject_ref_digest != expected_subject:
                raise PrivacyExportError("Lead does not match the privacy request.")

            touches = list(
                lead.attribution_touches.order_by("occurred_at", "created_at").values(
                    *ATTRIBUTION_EXPORT_FIELDS
                )
            )
            for touch in touches:
                touch["occurred_at"] = touch["occurred_at"].isoformat()
            conversion = getattr(lead, "conversion", None)
            contact_records = {item.id: item for item in lead.contacts.all()}
            accounts = []
            opportunities = []
            conversions = []
            if conversion is not None:
                if conversion.contact is not None:
                    contact_records[conversion.contact_id] = conversion.contact
                accounts.append(
                    serialize_model_fields(conversion.account, ACCOUNT_EXPORT_FIELDS)
                )
                if conversion.opportunity is not None:
                    opportunities.append(
                        serialize_model_fields(
                            conversion.opportunity, OPPORTUNITY_EXPORT_FIELDS
                        )
                    )
                conversion_record = serialize_model_fields(
                    conversion, CONVERSION_EXPORT_FIELDS
                )
                conversion_record.update(
                    {
                        "lead_id": str(conversion.lead_id),
                        "account_id": str(conversion.account_id),
                        "contact_id": (
                            str(conversion.contact_id)
                            if conversion.contact_id
                            else None
                        ),
                        "opportunity_id": (
                            str(conversion.opportunity_id)
                            if conversion.opportunity_id
                            else None
                        ),
                    }
                )
                conversions.append(conversion_record)
            contacts = [
                serialize_model_fields(item, CONTACT_EXPORT_FIELDS)
                for item in sorted(
                    contact_records.values(),
                    key=lambda item: (item.created_at, item.id),
                )
            ]
            tasks = [
                serialize_model_fields(item, TASK_EXPORT_FIELDS)
                for item in lead.lead_tasks.order_by("created_at", "id")
            ]

            from django.contrib.contenttypes.models import ContentType

            from common.models import Activity, Attachments

            lead_type = ContentType.objects.get_for_model(Lead)
            attachments = [
                serialize_model_fields(item, ATTACHMENT_METADATA_FIELDS)
                for item in Attachments.objects.filter(
                    org=org, content_type=lead_type, object_id=lead.id
                ).order_by("created_at", "id")
            ]
            activities = [
                serialize_model_fields(item, ACTIVITY_EXPORT_FIELDS)
                for item in Activity.objects.filter(
                    org=org, entity_type="Lead", entity_id=lead.id
                ).order_by("created_at", "id")
            ]
            for collection in (
                contacts,
                accounts,
                opportunities,
                conversions,
                tasks,
                attachments,
                activities,
            ):
                for item in collection:
                    item["id"] = str(item["id"])
            envelope = {
                "schema_version": "1.0",
                "inventory_version": INVENTORY_VERSION,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "request_ref": str(privacy_request.id),
                "records": {
                    "lead": _lead_payload(lead),
                    "attribution_touches": touches,
                    "contacts": contacts,
                    "accounts": accounts,
                    "opportunities": opportunities,
                    "lead_conversions": conversions,
                    "tasks": tasks,
                    "attachment_metadata": attachments,
                    "activities": activities,
                },
            }
            plaintext = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            if len(plaintext) > MAX_PLAINTEXT_BYTES:
                raise PrivacyExportError("Export exceeds the local safety limit.")
            aad = f"{org.id}:{privacy_request.id}:{artifact_ref}".encode()
            nonce = os.urandom(12)
            encrypted = (
                MAGIC + nonce + AESGCM(encryption_key).encrypt(nonce, plaintext, aad)
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(output_path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as artifact:
                artifact.write(encrypted)
                artifact.flush()
                os.fsync(artifact.fileno())
            created = True
            privacy_request.version += 1
            privacy_request.updated_by = actor
            privacy_request.save(update_fields=["version", "updated_by", "updated_at"])
            DataSubjectRequestEvent.objects.create(
                request=privacy_request,
                org=org,
                sequence=privacy_request.version,
                event_type="export_prepared",
                evidence_ref_digest=_digest(artifact_ref.encode()),
                actor_ref_digest=_digest(str(actor.id).encode()),
                created_by=actor,
            )
        return {
            "artifact_ref": artifact_ref,
            "artifact_path": output_path,
            "ciphertext_digest": _digest(encrypted),
            "expires_at": expires_at,
            "aad": aad,
        }
    except (OSError, ValidationError) as exc:
        if created:
            output_path.unlink(missing_ok=True)
        raise PrivacyExportError("Export could not be prepared safely.") from exc
    except Exception:
        if created:
            output_path.unlink(missing_ok=True)
        raise
