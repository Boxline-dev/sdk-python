"""Project secrets and saved login details: a secret exported into a shell session (its length checked, its value hidden
in the output), changed, listed, audited and deleted; then login details with 2FA on a saved login. The secret's value is
random and never printed.

    BOXLINE_API_KEY=bxl_… python examples/secrets.py
"""

import secrets as rnd

from boxline import Boxline

with Boxline() as bx:
    name = "EXAMPLE_" + rnd.token_hex(3).upper()
    bx.secrets.create(name, rnd.token_urlsafe(24), scope="shell", description="examples/secrets.py")
    try:
        with bx.sessions.create(browser=False, shell=True, timeout=120, env={"REGION": "eu"}, secrets=[name]) as s:
            print(f"session {s.id} exports {s.data.get('secrets')}; env {s.data.get('env')}")
            length = s.exec(f"printenv {name} | wc -c")["stdout"].strip()
            print(f"the value is {int(length) - 1} characters long in the shell")
            print(f"echo shows {s.exec(f'echo ${name}')['stdout'].strip()}: the value is hidden in the output")
        bx.secrets.update(name, value=rnd.token_urlsafe(24), description=None)
        for x in bx.secrets.list():
            print(f"secret {x['name']}: scope {x['scope']}, last used {x['lastUsedAt'] or 'never'}")
    finally:
        bx.secrets.delete(name)
    for e in bx.secrets.audit(name=name):
        print(f"audit: {e['at']} {e['action']} by {e.get('actor') or (e.get('usedBy') or {}).get('type')}")

    # Login details on a saved login: %login.username%, %login.password% and %login.otp% in sessions started with it.
    context = bx.contexts.create("examples/secrets.py")
    try:
        # totp_secret: a site's 2FA setup key (this one is the well-known test key).
        login = bx.contexts.set_login(context["id"], "https://example.com", "ada@example.com", rnd.token_urlsafe(12), totp_secret="JBSWY3DPEHPK3PXP")
        print(f"login details: {login['login']}")
        bx.contexts.delete_login(context["id"])
    finally:
        bx.contexts.delete(context["id"])
