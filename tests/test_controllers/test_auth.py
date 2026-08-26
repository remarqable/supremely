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
