import time

import pytest

from app.core.exceptions import ConflictError, ValidationError
from app.core.security import get_password_hash
from app.domain.enums import OrganizationStatus, SiteStatus
from app.domain.state_machines import assert_transition
from app.services.identity_access_service import _generate_totp, _verify_totp
from app.services.onboarding_service import ORG_TRANSITIONS, SITE_TRANSITIONS
from app.services.people_service import _mask_email


def test_onboarding_state_machines() -> None:
    assert_transition(ORG_TRANSITIONS, OrganizationStatus.DRAFT.value, OrganizationStatus.SUBMITTED.value)
    assert_transition(
        ORG_TRANSITIONS, OrganizationStatus.SUBMITTED.value, OrganizationStatus.NEEDS_INFORMATION.value
    )
    assert_transition(
        ORG_TRANSITIONS, OrganizationStatus.NEEDS_INFORMATION.value, OrganizationStatus.SUBMITTED.value
    )
    assert_transition(ORG_TRANSITIONS, OrganizationStatus.SUBMITTED.value, OrganizationStatus.ACTIVE.value)

    with pytest.raises(ConflictError):
        assert_transition(ORG_TRANSITIONS, OrganizationStatus.DRAFT.value, OrganizationStatus.ACTIVE.value)
    with pytest.raises(ConflictError):
        assert_transition(ORG_TRANSITIONS, OrganizationStatus.REJECTED.value, OrganizationStatus.ACTIVE.value)

    assert_transition(SITE_TRANSITIONS, SiteStatus.DRAFT.value, SiteStatus.SUBMITTED.value)
    assert_transition(SITE_TRANSITIONS, SiteStatus.SUBMITTED.value, SiteStatus.ACTIVE.value)
    with pytest.raises(ConflictError):
        assert_transition(SITE_TRANSITIONS, SiteStatus.DRAFT.value, SiteStatus.ACTIVE.value)


def test_totp_generation_and_masking_and_password_byte_limit() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    code = _generate_totp(secret, int(time.time() // 30))
    assert len(code) == 6
    assert _verify_totp(secret, code) is True
    assert _verify_totp(secret, "000000") is False or code == "000000"

    assert _mask_email("qa.officer@ojasyn.example") == "q***r@ojasyn.example"
    assert _mask_email("ab@x.io") == "a***@x.io"

    with pytest.raises(ValidationError):
        get_password_hash("x" * 73)
