"""Accounts, sessions, and the isolation they exist to provide.

Until users existed every request read one fridge. These tests pin the properties
that make a second account safe: a missing cookie is 401, another user's id is
404 rather than 403 (so guessing cannot map someone else's kitchen), and
analytics and chat cannot see across the boundary.
"""

from datetime import timedelta

from app.core import clock
from app.models.conversation import Conversation
from app.models.inventory import InventoryItem
from app.models.user import Session
from app.services.auth import (
    COOKIE_NAME,
    DEMO_USER_ID,
    authenticate,
    create_session,
    create_user,
    ensure_demo_user,
    hash_password,
    normalize_email,
    revoke_session,
    user_for_token,
    verify_password,
)
from app.services.disposition import apply_disposition


def register(
    client,
    email="new@local",
    password="password1",
    timezone="UTC",
    username=None,
):
    if username is None:
        username = email.split("@")[0]
    return client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "timezone": timezone,
            "username": username,
        },
    )


def login(client, email, password):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


class TestPasswordHashing:
    def test_a_hash_cannot_be_reversed_into_the_password(self):
        hashed = hash_password("password1")
        assert hashed != "password1"
        assert verify_password("password1", hashed) is True
        assert verify_password("wrongpass", hashed) is False

    def test_two_hashes_of_the_same_password_differ(self):
        """A stolen hash file should not reveal which accounts share a password."""
        assert hash_password("password1") != hash_password("password1")

    def test_a_corrupt_hash_is_a_miss_not_a_crash(self):
        assert verify_password("password1", "not-a-bcrypt-hash") is False


class TestUserService:
    def test_email_is_normalised(self, db):
        user = create_user(db, email="  Juhi@Local ", password="password1")
        assert user.email == "juhi@local"

    def test_a_short_password_is_refused(self, db):
        import pytest
        from app.services.auth import AuthError

        with pytest.raises(AuthError, match="8"):
            create_user(db, email="short@local", password="short")

    def test_an_invalid_email_is_refused(self, db):
        import pytest
        from app.services.auth import AuthError

        with pytest.raises(AuthError, match="email"):
            create_user(db, email="not-an-email", password="password1")

    def test_a_supplied_id_is_kept(self, db):
        user = create_user(
            db, email="fixed@local", password="password1", user_id="fixed-id"
        )
        assert user.id == "fixed-id"

    def test_authenticate_is_case_insensitive_on_email(self, db):
        create_user(db, email="juhi@local", password="password1")
        assert authenticate(db, "JUHI@LOCAL", "password1") is not None
        assert authenticate(db, "juhi", "password1") is not None
        assert authenticate(db, "juhi@local", "wrongpass") is None
        assert authenticate(db, "nobody@local", "password1") is None

    def test_a_taken_username_is_refused(self, db):
        import pytest
        from app.services.auth import AuthError

        create_user(db, email="one@local", password="password1", username="taken")
        with pytest.raises(AuthError, match="username"):
            create_user(
                db, email="two@local", password="password1", username="taken"
            )

    def test_an_invalid_username_is_refused(self, db):
        import pytest
        from app.services.auth import AuthError

        with pytest.raises(AuthError, match="Username"):
            create_user(
                db, email="odd@local", password="password1", username="no spaces"
            )

    def test_ensure_demo_user_is_stable(self, db):
        first = ensure_demo_user(db)
        second = ensure_demo_user(db)
        assert first.id == second.id == DEMO_USER_ID
        assert first.email == "juhi@local"
        assert first.username == "juhi"
        assert authenticate(db, "juhi", "shelfit") is first
        assert authenticate(db, "juhi@local", "shelfit") is first

    def test_ensure_demo_user_restores_the_published_password(self, db):
        """Register is 8+ characters; the demo password is not, and must stay usable."""
        user = ensure_demo_user(db)
        user.password_hash = hash_password("different1")
        db.commit()
        assert authenticate(db, "juhi", "shelfit") is None
        restored = ensure_demo_user(db)
        assert authenticate(db, "juhi", "shelfit") is restored
        assert authenticate(db, "juhi@local", "shelfit") is restored

    def test_ensure_demo_user_finds_an_existing_email(self, db):
        """A row created under the demo email, but not the stable id."""
        existing = create_user(
            db, email="juhi@local", password="password1", user_id="other-id"
        )
        assert ensure_demo_user(db) is existing

    def test_revoking_an_unknown_token_is_a_no_op(self, db):
        revoke_session(db, "no-such-token")
        revoke_session(db, None)


class TestRegisterAndLogin:
    def test_register_returns_the_user_and_sets_an_httponly_cookie(
        self, anonymous_client
    ):
        response = register(anonymous_client)
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "new@local"
        assert body["username"] == "new"
        assert body["has_password"] is True
        assert body["timezone"] == "UTC"
        assert "password" not in body
        assert "password_hash" not in body
        cookie = response.headers.get("set-cookie", "")
        assert COOKIE_NAME in cookie
        assert "httponly" in cookie.lower()

    def test_register_signs_the_client_in(self, anonymous_client):
        register(anonymous_client)
        me = anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "new@local"

    def test_a_taken_email_is_409(self, anonymous_client, user):
        response = register(
            anonymous_client, email=user.email, username="someoneelse"
        )
        assert response.status_code == 409

    def test_a_taken_username_is_409(self, anonymous_client, user):
        response = register(
            anonymous_client, email="other@local", username=user.username
        )
        assert response.status_code == 409

    def test_an_unknown_timezone_is_422(self, anonymous_client):
        response = register(anonymous_client, timezone="Not/AZone")
        assert response.status_code == 422

    def test_a_short_password_is_422(self, anonymous_client):
        response = register(anonymous_client, password="short")
        assert response.status_code == 422

    def test_login_does_not_enforce_the_register_length_rule(
        self, db, anonymous_client
    ):
        """The demo password is 7 characters. Login must not reject it as too short."""
        ensure_demo_user(db)
        response = login(anonymous_client, "juhi@local", "shelfit")
        assert response.status_code == 200
        assert response.json()["username"] == "juhi"

    def test_login_restores_a_drifted_demo_password(self, db, anonymous_client):
        user = ensure_demo_user(db)
        user.password_hash = hash_password("different1")
        db.commit()
        response = login(anonymous_client, "juhi", "shelfit")
        assert response.status_code == 200
        assert response.json()["id"] == user.id

    def test_demo_login_is_refused_when_disabled(self, db, anonymous_client, monkeypatch):
        from app.core import config

        ensure_demo_user(db)
        monkeypatch.setattr(config.settings, "enable_demo_login", False)
        response = login(anonymous_client, "juhi", "shelfit")
        assert response.status_code == 401
        assert anonymous_client.get("/api/auth/me").status_code == 401

    def test_login_accepts_the_right_password(self, anonymous_client, user):
        response = login(anonymous_client, "test@local", "testpass1")
        assert response.status_code == 200
        assert response.json()["email"] == "test@local"
        assert anonymous_client.get("/api/auth/me").status_code == 200

    def test_login_accepts_the_username(self, anonymous_client, user):
        response = login(anonymous_client, user.username, "testpass1")
        assert response.status_code == 200
        assert response.json()["username"] == user.username

    def test_bad_email_and_bad_password_look_the_same(self, anonymous_client, user):
        """A guess must not be able to tell which of the two it hit."""
        missing = login(anonymous_client, "nobody@local", "testpass1")
        wrong = login(anonymous_client, "test@local", "wrongpass")
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["detail"] == wrong.json()["detail"]

    def test_me_requires_a_session(self, anonymous_client):
        assert anonymous_client.get("/api/auth/me").status_code == 401

    def test_logout_revokes_the_session(self, client):
        assert client.get("/api/auth/me").status_code == 200
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert client.get("/api/auth/me").status_code == 401

    def test_logout_without_a_cookie_is_still_ok(self, anonymous_client):
        assert anonymous_client.post("/api/auth/logout").status_code == 200

    def test_an_expired_session_is_rejected(self, db, anonymous_client, user):
        token = create_session(db, user)
        row = db.get(Session, token)
        row.expires_at = clock.utcnow() - timedelta(seconds=1)
        db.commit()
        anonymous_client.cookies.set(COOKIE_NAME, token)
        assert anonymous_client.get("/api/auth/me").status_code == 401
        assert user_for_token(db, token) is None

    def test_revoking_a_token_makes_it_useless(self, db, user):
        token = create_session(db, user)
        revoke_session(db, token)
        assert user_for_token(db, token) is None


class TestProtectedRoutes:
    def test_inventory_requires_a_session(self, anonymous_client):
        assert anonymous_client.get("/api/inventory/").status_code == 401
        assert (
            anonymous_client.post(
                "/api/inventory/", json={"name": "Milk"}
            ).status_code
            == 401
        )

    def test_chat_requires_a_session(self, anonymous_client):
        assert (
            anonymous_client.post("/api/chat/", json={"message": "hi"}).status_code
            == 401
        )

    def test_waste_report_requires_a_session(self, anonymous_client):
        assert anonymous_client.get("/api/analytics/waste").status_code == 401


class TestIsolation:
    """Another user's ids must look like they do not exist."""

    def other_user(self, db):
        return create_user(db, email="other@local", password="password1")

    def other_item(self, db, name="Secret Milk"):
        item = InventoryItem(
            name=name,
            quantity=1.0,
            unit="l",
            user_id=self.other_user(db).id,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_list_does_not_include_another_users_items(self, db, client):
        self.other_item(db)
        client.post("/api/inventory/", json={"name": "My Milk"})
        names = {item["name"] for item in client.get("/api/inventory/").json()}
        assert names == {"My Milk"}

    def test_another_users_item_is_404_not_403(self, db, client):
        item = self.other_item(db)
        assert client.get(f"/api/inventory/{item.id}").status_code == 404
        assert client.delete(f"/api/inventory/{item.id}").status_code == 404
        assert (
            client.patch(
                f"/api/inventory/{item.id}", json={"name": "Stolen"}
            ).status_code
            == 404
        )

    def test_waste_report_does_not_include_another_users_events(self, db, client):
        item = self.other_item(db, name="Their Lettuce")
        apply_disposition(db, item, outcome="wasted", reason="slimy")
        db.commit()
        report = client.get("/api/analytics/waste").json()
        assert report["wasted"]["events"] == 0
        assert report["by_name"] == []

    def test_another_users_conversation_is_404(self, db, client):
        conversation = Conversation(user_id=self.other_user(db).id)
        db.add(conversation)
        db.commit()
        assert (
            client.get(f"/api/chat/conversations/{conversation.id}").status_code
            == 404
        )
        assert (
            client.delete(f"/api/chat/conversations/{conversation.id}").status_code
            == 404
        )

    def test_created_items_belong_to_the_signed_in_user(self, db, client, user):
        body = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        item = db.get(InventoryItem, body["id"])
        assert item.user_id == user.id


class TestTimezoneOnRequests:
    def test_reminders_ask_today_in_the_users_zone(self, client, user, monkeypatch):
        """The value that used to be UTC for everyone now comes from the account."""
        user.timezone = "America/New_York"
        seen = []
        real = clock.today

        def spy(tz_name=None):
            seen.append(tz_name)
            return real(tz_name)

        monkeypatch.setattr("app.api.endpoints.inventory.clock.today", spy)
        assert client.get("/api/inventory/reminders/?days=7").status_code == 200
        assert seen == ["America/New_York"]


def test_normalize_email_strips_and_lowers():
    assert normalize_email("  A@B.C  ") == "a@b.c"


class TestUsernameHelpers:
    def test_punctuation_is_stripped_and_short_names_are_padded(self):
        from app.services.auth import username_from_email

        assert username_from_email("ab@x.com") == "abuser"

    def test_a_leading_symbol_gets_a_prefix(self):
        from app.services.auth import username_from_email

        assert username_from_email("_x@x.com") == "user_x"

    def test_a_collision_gets_a_suffix(self, db):
        from app.services.auth import allocate_username

        create_user(db, email="one@local", password="password1", username="juice")
        assert allocate_username(db, "juice") == "juice2"

    def test_login_without_an_identifier_is_422(self, anonymous_client):
        response = anonymous_client.post(
            "/api/auth/login", json={"password": "password1"}
        )
        assert response.status_code == 422


class TestAuthProviders:
    def test_google_is_off_by_default(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "google_client_id", None)
        monkeypatch.setattr(config.settings, "google_client_secret", None)
        body = anonymous_client.get("/api/auth/providers").json()
        assert body["google"] is False
        assert body["demo"] is True

    def test_google_is_advertised_when_configured(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "google_client_id", "id.apps.googleusercontent.com")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")
        body = anonymous_client.get("/api/auth/providers").json()
        assert body["google"] is True
        assert body["demo"] is True

    def test_demo_is_hidden_when_disabled(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "enable_demo_login", False)
        assert anonymous_client.get("/api/auth/providers").json()["demo"] is False


class TestGoogleSignIn:
    def test_start_is_unavailable_without_credentials(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "google_client_id", None)
        monkeypatch.setattr(config.settings, "google_client_secret", None)
        assert anonymous_client.get("/api/auth/google").status_code == 503

    def test_start_redirects_to_google(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "google_client_id", "id.apps.googleusercontent.com")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")
        response = anonymous_client.get("/api/auth/google", follow_redirects=False)
        assert response.status_code == 302
        assert "accounts.google.com" in response.headers["location"]
        assert "shelfit_oauth_state" in response.headers.get("set-cookie", "")

    def test_callback_creates_a_google_only_account(
        self, anonymous_client, db, monkeypatch
    ):
        from app.core import config
        from app.api.endpoints import auth as auth_endpoint
        from app.services.google_oauth import GoogleIdentity

        monkeypatch.setattr(config.settings, "google_client_id", "id")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")
        monkeypatch.setattr(
            auth_endpoint,
            "fetch_google_identity",
            lambda code: GoogleIdentity(subject="sub-1", email="person@gmail.com"),
        )

        start = anonymous_client.get("/api/auth/google", follow_redirects=False)
        state = start.cookies["shelfit_oauth_state"]
        response = anonymous_client.get(
            f"/api/auth/google/callback?code=ok&state={state}",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "auth_error" not in response.headers["location"]
        me = anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "person@gmail.com"
        assert me.json()["has_password"] is False
        assert authenticate(db, "person@gmail.com", "anything1") is None

    def test_callback_links_an_existing_email(self, anonymous_client, db, monkeypatch):
        from app.core import config
        from app.api.endpoints import auth as auth_endpoint
        from app.services.google_oauth import GoogleIdentity

        existing = create_user(db, email="person@gmail.com", password="password1")
        monkeypatch.setattr(config.settings, "google_client_id", "id")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")
        monkeypatch.setattr(
            auth_endpoint,
            "fetch_google_identity",
            lambda code: GoogleIdentity(subject="sub-1", email="person@gmail.com"),
        )
        start = anonymous_client.get("/api/auth/google", follow_redirects=False)
        state = start.cookies["shelfit_oauth_state"]
        anonymous_client.get(
            f"/api/auth/google/callback?code=ok&state={state}",
            follow_redirects=False,
        )
        db.refresh(existing)
        assert existing.google_id == "sub-1"
        assert authenticate(db, "person@gmail.com", "password1") is not None

    def test_a_google_exchange_failure_returns_to_the_frontend(
        self, anonymous_client, monkeypatch
    ):
        from app.core import config
        from app.api.endpoints import auth as auth_endpoint
        from app.services.google_oauth import GoogleOAuthError

        monkeypatch.setattr(config.settings, "google_client_id", "id")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")

        def _fail(_code):
            raise GoogleOAuthError("Google sign-in failed")

        monkeypatch.setattr(auth_endpoint, "fetch_google_identity", _fail)
        start = anonymous_client.get("/api/auth/google", follow_redirects=False)
        state = start.cookies["shelfit_oauth_state"]
        response = anonymous_client.get(
            f"/api/auth/google/callback?code=ok&state={state}",
            follow_redirects=False,
        )
        assert "auth_error" in response.headers["location"]

    def test_a_mismatched_state_is_rejected(self, anonymous_client, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "google_client_id", "id")
        monkeypatch.setattr(config.settings, "google_client_secret", "secret")
        anonymous_client.get("/api/auth/google", follow_redirects=False)
        response = anonymous_client.get(
            "/api/auth/google/callback?code=ok&state=forged",
            follow_redirects=False,
        )
        assert "auth_error" in response.headers["location"]
        assert anonymous_client.get("/api/auth/me").status_code == 401


class TestGoogleIdentityExchange:
    def test_a_verified_email_is_accepted(self, monkeypatch):
        from types import SimpleNamespace

        from app.services import google_oauth

        monkeypatch.setattr(
            google_oauth.requests,
            "post",
            lambda *a, **k: SimpleNamespace(
                status_code=200, json=lambda: {"access_token": "tok"}
            ),
        )
        monkeypatch.setattr(
            google_oauth.requests,
            "get",
            lambda *a, **k: SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "sub": "sub-1",
                    "email": "A@Gmail.com",
                    "email_verified": True,
                },
            ),
        )
        identity = google_oauth.fetch_google_identity("code")
        assert identity.email == "a@gmail.com"
        assert identity.subject == "sub-1"

    def test_a_failed_token_exchange_is_refused(self, monkeypatch):
        from types import SimpleNamespace

        import pytest

        from app.services import google_oauth

        monkeypatch.setattr(
            google_oauth.requests,
            "post",
            lambda *a, **k: SimpleNamespace(status_code=400, json=lambda: {}),
        )
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth.fetch_google_identity("code")

    def test_a_token_without_access_is_refused(self, monkeypatch):
        from types import SimpleNamespace

        import pytest

        from app.services import google_oauth

        monkeypatch.setattr(
            google_oauth.requests,
            "post",
            lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {}),
        )
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth.fetch_google_identity("code")

    def test_userinfo_failure_is_refused(self, monkeypatch):
        from types import SimpleNamespace

        import pytest

        from app.services import google_oauth

        monkeypatch.setattr(
            google_oauth.requests,
            "post",
            lambda *a, **k: SimpleNamespace(
                status_code=200, json=lambda: {"access_token": "tok"}
            ),
        )
        monkeypatch.setattr(
            google_oauth.requests,
            "get",
            lambda *a, **k: SimpleNamespace(status_code=401, json=lambda: {}),
        )
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth.fetch_google_identity("code")

    def test_an_unverified_email_is_refused(self, monkeypatch):
        from types import SimpleNamespace

        import pytest

        from app.services import google_oauth

        monkeypatch.setattr(
            google_oauth.requests,
            "post",
            lambda *a, **k: SimpleNamespace(
                status_code=200, json=lambda: {"access_token": "tok"}
            ),
        )
        monkeypatch.setattr(
            google_oauth.requests,
            "get",
            lambda *a, **k: SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "sub": "sub-1",
                    "email": "a@gmail.com",
                    "email_verified": False,
                },
            ),
        )
        with pytest.raises(google_oauth.GoogleOAuthError):
            google_oauth.fetch_google_identity("code")

    def test_a_returning_google_user_is_found_by_subject(self, db):
        from app.models.user import User
        from app.services.google_oauth import GoogleIdentity, user_from_google

        user = User(
            email="g@gmail.com",
            username="guser",
            password_hash=None,
            google_id="sub-1",
            timezone="UTC",
        )
        db.add(user)
        db.commit()
        found = user_from_google(db, GoogleIdentity("sub-1", "g@gmail.com"))
        assert found.id == user.id

    def test_a_conflicting_google_link_is_refused(self, db):
        import pytest
        from app.services.google_oauth import GoogleIdentity, GoogleOAuthError, user_from_google

        existing = create_user(db, email="person@gmail.com", password="password1")
        existing.google_id = "other-sub"
        db.add(existing)
        db.commit()
        with pytest.raises(GoogleOAuthError, match="already linked"):
            user_from_google(
                db, GoogleIdentity("sub-1", "person@gmail.com")
            )
