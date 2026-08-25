"""Phase 6: newsletter. Completion test: an Organization publishes a Post
simultaneously to its website and its subscriber audience."""

from flask import g

from app.extensions import db
from app.models import (Delivery, DeliveryRecipient, InstallationSetting,
                        Job, Post, Subscriber)
from app.platform import mailer
from app.platform.jobs import run_pending_jobs
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def configure_email(app):
    InstallationSetting.set('email.smtp_host', 'smtp.test')
    InstallationSetting.set('email.from_address', 'news@acme.test')
    app.config['MAIL_SUPPRESS_SEND'] = True
    mailer._outbox.clear()


def add_subscribers(app, org, emails, status='subscribed'):
    with app.test_request_context():
        g.org = org
        for email in emails:
            Subscriber.subscribe(email, org.id, require_confirmation=False)


def test_subscribe_without_email_is_immediate(client, acme, globex):
    response = client.post('/subscribe', base_url=ACME,
                           data={'email': 'reader@example.com'})
    assert response.status_code == 200
    subscriber = Subscriber.query.filter_by(email='reader@example.com').first()
    assert subscriber.status == 'subscribed'    # no email -> no double opt-in


def test_subscribe_with_email_requires_confirmation(app, client, acme, globex):
    configure_email(app)
    client.post('/subscribe', base_url=ACME,
                data={'email': 'reader@example.com'})
    subscriber = Subscriber.query.filter_by(email='reader@example.com').first()
    assert subscriber.status == 'pending'
    assert Job.query.filter_by(name='newsletter.confirmation_email').count() == 1

    run_pending_jobs()
    assert len(mailer._outbox) == 1
    assert 'Confirm' in mailer._outbox[0]['Subject']

    # GET shows a confirm page and does NOT mutate (prefetch-safe)
    page = client.get(f'/subscribe/confirm/{subscriber.token}', base_url=ACME)
    assert page.status_code == 200
    assert db.session.get(Subscriber, subscriber.id).status == 'pending'
    # POST performs the confirmation
    response = client.post(f'/subscribe/confirm/{subscriber.token}',
                           base_url=ACME)
    assert response.status_code == 302
    assert db.session.get(Subscriber, subscriber.id).status == 'subscribed'


def test_invalid_email_rejected(client, acme, globex):
    response = client.post('/subscribe', base_url=ACME,
                           data={'email': 'not-an-email'})
    assert response.status_code == 400
    assert Subscriber.query.count() == 0


def test_duplicate_subscribe_dedupes(app, client, acme, globex):
    client.post('/subscribe', base_url=ACME, data={'email': 'r@example.com'})
    client.post('/subscribe', base_url=ACME, data={'email': 'R@example.com '})
    assert Subscriber.query.count() == 1


def test_unsubscribe(app, client, acme, globex):
    client.post('/subscribe', base_url=ACME, data={'email': 'r@example.com'})
    subscriber = Subscriber.query.first()
    # GET shows a confirmation page and does NOT unsubscribe (prefetch-safe)
    page = client.get(f'/unsubscribe/{subscriber.token}', base_url=ACME)
    assert page.status_code == 200
    assert db.session.get(Subscriber, subscriber.id).status == 'subscribed'
    # POST performs the unsubscribe
    response = client.post(f'/unsubscribe/{subscriber.token}', base_url=ACME)
    assert response.status_code == 200
    assert db.session.get(Subscriber, subscriber.id).status == 'unsubscribed'

    # Re-subscribing revives the same row
    client.post('/subscribe', base_url=ACME, data={'email': 'r@example.com'})
    assert Subscriber.query.count() == 1
    assert Subscriber.query.first().status == 'subscribed'


def test_publish_to_web_and_email_simultaneously(app, client, acme, globex, user):
    """The Phase 6 completion test."""
    configure_email(app)
    add_subscribers(app, acme, ['a@example.com', 'b@example.com',
                                'c@example.com'])
    # One unsubscribed straggler who must NOT receive it
    with app.test_request_context():
        g.org = acme
        Subscriber.subscribe('gone@example.com', acme.id, False).unsubscribe()

    login_as(client, user)
    client.post('/manage/posts/new', base_url=ACME, data={
        'title': 'Big News', 'slug': 'big-news',
        'body': 'We **shipped** it.', 'visibility': 'public',
        'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Post.published_by_slug('big-news')
        post_id = post.id

    response = client.post(f'/manage/posts/{post_id}/send-newsletter',
                           base_url=ACME, follow_redirects=True)
    assert b'Queued for delivery to 3 subscribers' in response.data

    # Web is live immediately
    web = app.test_client().get('/posts/big-news', base_url=ACME)
    assert b'shipped' in web.data

    # The worker delivers
    run_pending_jobs()
    delivery = Delivery.query.first()
    assert delivery.status == 'done'
    assert delivery.sent_count == 3
    assert delivery.failed_count == 0
    assert len(mailer._outbox) == 3
    recipients = {message['To'] for message in mailer._outbox}
    assert recipients == {'a@example.com', 'b@example.com', 'c@example.com'}

    message = mailer._outbox[0]
    assert message['Subject'] == 'Big News'
    body = message.get_body(('html',)).get_content()
    assert '/posts/big-news' in body
    assert '/unsubscribe/' in body


def test_delivery_is_idempotent_after_partial_failure(app, client, acme,
                                                      globex, user):
    configure_email(app)
    add_subscribers(app, acme, ['ok1@example.com', 'ok2@example.com'])
    login_as(client, user)
    client.post('/manage/posts/new', base_url=ACME, data={
        'title': 'P', 'slug': 'p', 'body': 'x', 'visibility': 'public',
        'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post_id = Post.published_by_slug('p').id
    client.post(f'/manage/posts/{post_id}/send-newsletter', base_url=ACME)

    # First run partially "crashes": simulate by marking one recipient sent
    delivery = Delivery.query.first()
    first = DeliveryRecipient.query.filter_by(delivery_id=delivery.id).first()
    from app.models.base import utcnow
    first.sent_at = utcnow()
    db.session.commit()

    run_pending_jobs()
    # Only the unsent recipient was mailed
    assert len(mailer._outbox) == 1
    assert Delivery.query.first().sent_count == 2


def test_send_without_email_config_fails_gracefully(app, client, acme, globex,
                                                    user):
    add_subscribers(app, acme, ['a@example.com'])
    login_as(client, user)
    client.post('/manage/posts/new', base_url=ACME, data={
        'title': 'P2', 'slug': 'p2', 'body': 'x', 'visibility': 'public',
        'action': 'publish'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post_id = Post.published_by_slug('p2').id
    response = client.post(f'/manage/posts/{post_id}/send-newsletter',
                           base_url=ACME, follow_redirects=True)
    assert b'Configure email' in response.data
    assert Delivery.query.count() == 0
    # Publishing itself remains fully operable
    assert app.test_client().get('/posts/p2', base_url=ACME).status_code == 200


def test_subscribers_tenant_isolated(app, client, acme, globex):
    client.post('/subscribe', base_url=ACME, data={'email': 'r@example.com'})
    other = app.test_client()
    other.post('/subscribe', base_url='http://globex.example.test',
               data={'email': 'r@example.com'})
    # Same email may subscribe to both orgs: two rows, each org sees one
    from app.platform.tenant import unscoped
    with app.test_request_context():
        with unscoped():
            assert Subscriber.query.count() == 2
    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert Subscriber.query.count() == 1
