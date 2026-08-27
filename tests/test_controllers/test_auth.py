from tests.conftest import PASSWORD, login_as


def test_login_page_renders(client, app):
    response = client.get('/auth/login')
    assert response.status_code == 200
    assert b'password' in response.data.lower()


def test_login_wrong_password(client, user):
    response = client.post('/auth/login', data={
        'email': user.email, 'password': 'wrong-password',
    })
    assert response.status_code == 401
    assert b'Invalid email or password' in response.data


def test_login_unknown_email_same_message(client, app):
    response = client.post('/auth/login', data={
        'email': 'nobody@example.com', 'password': 'whatever-123',
    })
    assert response.status_code == 401
    assert b'Invalid email or password' in response.data


def test_login_success_redirects(client, user):
    response = client.post('/auth/login', data={
        'email': user.email, 'password': PASSWORD,
    })
    assert response.status_code == 302


def test_inactive_user_cannot_login(client, user):
    user.is_active = False
    user.save()
    response = client.post('/auth/login', data={
        'email': user.email, 'password': PASSWORD,
    })
    assert response.status_code == 401


def test_logout(client, user):
    login_as(client, user)
    response = client.post('/auth/logout', follow_redirects=False)
    assert response.status_code == 302


def test_logout_clears_remember_cookie(client, user):
    client.post('/auth/login', data={'email': user.email,
                                     'password': PASSWORD})
    client.post('/auth/logout', follow_redirects=False)

    cookies = {c.key for c in client._cookies.values() if c.value}
    assert 'remember_token' not in cookies

    assert client.get('/auth/login').status_code == 200


def test_change_password(client, user):
    login_as(client, user)
    response = client.post('/auth/password', data={
        'current_password': PASSWORD,
        'new_password': 'brand-new-secret-1',
        'confirm_password': 'brand-new-secret-1',
    })
    assert response.status_code == 302
    assert user.check_password('brand-new-secret-1')


def test_change_password_requires_current(client, user):
    login_as(client, user)
    client.post('/auth/password', data={
        'current_password': 'wrong',
        'new_password': 'brand-new-secret-1',
        'confirm_password': 'brand-new-secret-1',
    })
    assert user.check_password(PASSWORD)


def test_open_redirect_blocked(client, user):
    response = client.post('/auth/login?next=//evil.example.com', data={
        'email': user.email, 'password': PASSWORD,
    })
    assert 'evil' not in response.headers['Location']


def test_cli_password_reset(runner, user):
    result = runner.invoke(args=['users', 'reset-password', user.email])
    assert 'New password' in result.output
    new_password = result.output.split(':', 1)[1].split()[0]
    assert user.check_password(new_password)


def test_installation_admin_signs_in_with_its_username(client, app):
    """The account the first-boot wizard creates has no email address, so the
    login form must accept a bare username end to end."""
    from app.models import User

    User.create(email=User.INSTALL_ADMIN_USERNAME, name='Admin',
                password=PASSWORD, is_platform_admin=True)

    response = client.post('/auth/login', data={
        'email': User.INSTALL_ADMIN_USERNAME, 'password': PASSWORD})
    assert response.status_code == 302

    with client.session_transaction() as session:
        assert session.get('_user_id') is not None


def test_login_form_does_not_constrain_input_to_an_email(client, app):
    """type=email would make the browser reject the administrator username
    before the request is ever sent."""
    body = client.get('/auth/login').data
    assert b'type="email"' not in body


def _steal_remember_token(client, user):
    client.post('/auth/login', data={'email': user.email,
                                     'password': PASSWORD})
    return {c.key: c.value for c in client._cookies.values()}['remember_token']


def _replay(app, token):
    """A fresh client carrying nothing but the stolen remember cookie."""
    attacker = app.test_client()
    attacker.set_cookie('remember_token', token, domain='example.test')
    return attacker.get('/profile').status_code


def test_remember_cookie_authenticates_on_a_fresh_client(app, client, user):
    assert _replay(app, _steal_remember_token(client, user)) == 200


def test_password_change_revokes_an_issued_remember_cookie(app, client, user):
    token = _steal_remember_token(client, user)
    assert _replay(app, token) == 200

    user.set_password('brand-new-secret-1')
    user.save()

    # The remember cookie carries no server-side record, so the session id
    # embeds a digest of the password: changing it strips every copy already
    # handed out, not just the one in the owner's browser.
    assert _replay(app, token) == 302


def test_password_change_keeps_the_current_session_signed_in(client, user):
    login_as(client, user)
    client.post('/auth/password', data={
        'current_password': PASSWORD,
        'new_password': 'brand-new-secret-1',
        'confirm_password': 'brand-new-secret-1',
    })
    assert client.get('/profile').status_code == 200


def test_session_id_without_a_stamp_is_rejected(app, user):
    from app.extensions import load_user
    with app.test_request_context():
        assert load_user(str(user.id)) is None       # pre-stamp cookie
        assert load_user(f'{user.id}:not-the-stamp') is None
        assert load_user(user.get_id()) is not None


def test_deactivation_blocks_an_issued_remember_cookie(app, client, user):
    """Load-bearing for session_auth_stamp: is_active is deliberately NOT in
    the stamp material, because UserMixin.is_authenticated already returns it.
    If that ever stops being true, the stamp must cover it again."""
    token = _steal_remember_token(client, user)
    assert _replay(app, token) == 200

    user.is_active = False
    user.save()
    assert _replay(app, token) == 302


def test_admin_resetting_their_own_password_stays_signed_in(app, client,
                                                            platform_admin):
    login_as(client, platform_admin)
    response = client.post(f'/admin/users/{platform_admin.id}/password',
                           data={'password': 'brand-new-secret-1'})
    assert response.status_code == 302
    assert client.get('/admin/').status_code == 200


def test_load_user_rejects_malformed_ids_without_raising(app, user):
    from app.extensions import load_user
    with app.test_request_context():
        # Non-ASCII written as escapes: str.isdigit() is True for
        # SUPERSCRIPT TWO and ARABIC-INDIC FIVE but int() rejects them,
        # and compare_digest raises TypeError on a non-ASCII str.
        for value in ('', ':', '5:', ':abc', 'abc:def', '5:a:b',
                      '\u00b2:abc', '\u0665:abc', f'{user.id}:\u00e9',
                      '9' * 5000 + ':abc'):
            assert load_user(value) is None, value
