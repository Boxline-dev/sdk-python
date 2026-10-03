"""Credentials: a secret exported into a shell session (its length checked, its value hidden in the output), changed,
listed, audited and deleted; then a website password with a 2FA key, linked to a browser profile. The values are random
(or the well-known test key) and never printed.

    BOXLINE_API_KEY=bxl_… python examples/credentials.py
"""

import secrets as rnd

from boxline import Boxline, FeatureNotInPlanError

with Boxline() as bx:
    name = "EXAMPLE_" + rnd.token_hex(3).upper()
    bx.credentials.create(name, "secret", value=rnd.token_urlsafe(24), scope="shell", description="examples/credentials.py")
    try:
        with bx.sessions.create(browser=False, shell=True, timeout=120, env={"REGION": "eu"}, credentials=[name]) as s:
            print(f"session {s.id} exports {s.data.get('credentials')}; env {s.data.get('env')}")
            length = s.exec(f"printenv {name} | wc -c")["stdout"].strip()
            print(f"the value is {int(length) - 1} characters long in the shell")
            print(f"echo shows {s.exec(f'echo ${name}')['stdout'].strip()}: the value is hidden in the output")
        bx.credentials.update(name, value=rnd.token_urlsafe(24), description=None)
        for c in bx.credentials.list():
            print(f"credential {c['name']} ({c['type']}): scope {c['scope']}, last used {c['lastUsedAt'] or 'never'}")
    finally:
        bx.credentials.delete(name)
    for e in bx.credentials.audit(name=name):
        print(f"audit: {e['at']} {e['action']} by {e.get('actor') or (e.get('usedBy') or {}).get('type')}")

    # A website password with a 2FA key, linked to a browser profile: sessions started with the profile (and the agent
    # runs in them) can sign in again by themselves with %SHOP.username%, %SHOP.password% and %SHOP.otp%.
    shop = "EXAMPLE_SHOP_" + rnd.token_hex(3).upper()
    profile = bx.profiles.create("examples/credentials.py")
    try:
        password = bx.credentials.create(
            shop,
            "password",
            origins=["https://example.com"],  # the only site the AI may type it on
            username="ada@example.com",
            password=rnd.token_urlsafe(12),
            totp_secret="JBSWY3DPEHPK3PXP",  # a site's 2FA setup key (this one is the well-known test key)
        )
        print(f"password {password['name']}: user {password['username']}, 2FA {'on' if password['hasTotp'] else 'off'}")
        linked = bx.profiles.update(profile["id"], credential=shop)
        print(f"profile {linked['id']} signs in with {linked['credential']}")
    except FeatureNotInPlanError:
        print("password credentials are not in this plan (loginDetails)")
    finally:
        bx.profiles.delete(profile["id"])
        try:
            bx.credentials.delete(shop)
        except Exception:  # not created (the plan has no passwords), or already gone
            pass
