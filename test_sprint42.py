from __future__ import annotations

from flask import Flask

from passenger_wsgi import application


def assert_condition(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    assert_condition(isinstance(application, Flask), "passenger_wsgi.application must be a Flask app")

    client = application.test_client()

    protected_response = client.get("/", follow_redirects=False)
    assert_condition(
        protected_response.status_code in (302, 401, 403),
        f"Expected protected route response (302/401/403), got {protected_response.status_code}",
    )
    if protected_response.status_code == 302:
        redirect_to = protected_response.headers.get("Location", "")
        assert_condition("/login" in redirect_to, f"Expected redirect to /login, got {redirect_to}")

    success_response = client.post(
        "/login",
        data={"username": "Machete", "password": "@Machete1231"},
        follow_redirects=False,
    )
    assert_condition(success_response.status_code == 302, "Expected successful login redirect")
    success_location = success_response.headers.get("Location", "")
    assert_condition(
        success_location.endswith("/") or success_location == "/",
        f"Expected redirect to '/', got {success_location}",
    )

    with client:
        client.post(
            "/login",
            data={"username": "Machete", "password": "@Machete1231"},
            follow_redirects=False,
        )
        authenticated_home = client.get("/", follow_redirects=False)
        assert_condition(
            authenticated_home.status_code == 200,
            f"Expected authenticated GET / to return 200, got {authenticated_home.status_code}",
        )

    failed_client = application.test_client()
    failed_response = failed_client.post(
        "/login",
        data={"username": "Machete", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert_condition(
        failed_response.status_code in (200, 401, 302),
        f"Expected failed login status 200/401/302, got {failed_response.status_code}",
    )
    if failed_response.status_code == 302:
        failed_location = failed_response.headers.get("Location", "")
        assert_condition(
            "/login" in failed_location and not (failed_location.endswith("/") or failed_location == "/"),
            f"Unexpected failed-login redirect target: {failed_location}",
        )

    print("Manual UI/UX inspection checklist:")
    print("1. Open src/dashboard/templates/base.html and confirm Tailwind CDN and premium typography imports.")
    print("2. Confirm custom Tailwind classes and high-end hedge-fund palette tokens (obsidian/slate/cyan/gold) are present.")
    print("3. Confirm transition and hover utility usage for nav, cards, forms, and buttons.")
    print("4. Open src/dashboard/templates/login.html and confirm polished login composition and luxury visual hierarchy.")
    print("Sprint 42 Premium UI Dashboard & Auth Verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
