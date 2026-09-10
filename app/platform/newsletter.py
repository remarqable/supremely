"""Newsletter sending: the job handlers and email composition.

All sending runs through the DB-backed jobs queue. Handlers are idempotent:
only recipients not yet marked sent get mailed, so a crash mid-batch resumes
instead of double-sending.
"""

from app.extensions import db
from app.models.base import utcnow
from app.platform.i18n import t
from app.platform.jobs import job
from app.platform.logger import get_logger

log = get_logger()

BATCH_LIMIT = 200       # per job execution; the job re-enqueues if more remain


def render_fields_for_email(content) -> tuple[str, str]:
    """(html table, plain text) for this item's fields.

    Rendered once per batch by the caller and passed in, not cached on the
    row: the worker does not remove the session between jobs, so anything
    stashed on an instance outlives the job that put it there and a resend
    after an edit would send the old render.
    """
    from app.platform.fields import render_fields, render_fields_text
    rows = render_fields(content, surface='email')
    table = (f'<table role="presentation" cellpadding="0" cellspacing="0" '
             f'style="margin:16px 0">{rows}</table>') if rows else ''
    return table, render_fields_text(content)


def compose_email(content, org, subscriber, fields=None,
                  logo=None) -> tuple[str, str, str]:
    """(subject, text, html) for one recipient.

    `fields` is the type's rendered fields, which are the same for every
    recipient of one delivery; the caller renders them once for the batch.
    Left out, they are rendered here, so a single send stays a single call.
    """
    from app.platform.tenant import org_url
    content_url = org_url(org, content.permalink)
    unsubscribe_url = org_url(org, f'/unsubscribe/{subscriber.token}')

    # The type's own fields, on both halves. A podcast email that omitted
    # the episode link was sending a title and a paragraph.
    field_table, field_text = (fields if fields is not None
                               else render_fields_for_email(content))

    subject = content.title
    text = (f'{content.title}\n\n{content.excerpt_or_summary(400)}\n\n'
            f'{field_text}\n'
            f'{t("newsletter.read_online")}: {content_url}\n\n--\n'
            f'{t("newsletter.why_you_get_this", org=org.name)}\n'
            f'{t("newsletter.unsubscribe")}: {unsubscribe_url}\n')
    # Mail clients drop an iframe, so a video renders as a link here rather
    # than as the blank space an embed would leave (platform/content.py).
    from app.platform.content import render_markdown
    from app.platform.emails import render_message
    body_html = render_markdown(content.body, embed_videos=False)
    # render_message, not render_email: it puts the attribution into both
    # halves. Building the text here and the markup there is how the text
    # part came to have none at all.
    text, html = render_message(
        'emails/newsletter.html', subject=subject, org=org, text=text,
        heading=content.title, preview=content.excerpt_or_summary(140),
        body_html=body_html, field_table=field_table,
        action=t('newsletter.read_online'), action_url=content_url,
        footer_note=t('newsletter.why_you_get_this', org=org.name),
        unsubscribe_url=unsubscribe_url,
        unsubscribe_label=t('newsletter.unsubscribe'),
        **({'logo_src': logo} if logo is not None else {}))
    return subject, text, html
@job('newsletter.send_delivery')
def send_delivery(payload: dict) -> None:
    from app.models import Content, Organization
    from app.models.newsletter import Delivery, DeliveryRecipient
    from app.platform.mailer import is_email_configured, send_email

    delivery = db.session.get(Delivery, payload.get('delivery_id'))
    if delivery is None or delivery.status == 'done':
        return
    if not is_email_configured():
        delivery.status = 'failed'
        db.session.commit()
        log.error('newsletter_send_no_email', delivery_id=delivery.id)
        return

    content = db.session.get(Content, delivery.content_id)
    org = db.session.get(Organization, delivery.org_id)
    if content is None or org is None:
        delivery.status = 'failed'
        db.session.commit()
        return

    delivery.status = 'sending'
    db.session.commit()

    unsent = (DeliveryRecipient.query
              .filter_by(delivery_id=delivery.id, sent_at=None, error=None)
              .limit(BATCH_LIMIT).all())
    # Once for the batch: the fields do not vary by recipient, and this
    # loop runs up to BATCH_LIMIT times.
    rendered_fields = render_fields_for_email(content)
    # Once per batch, not once per recipient: this is a query, and a batch
    # is two hundred messages. A string, because the commit after each
    # recipient expires a row and it would be fetched again anyway.
    from app.platform.emails import logo_src
    logo = logo_src(org)
    for recipient in unsent:
        subscriber = recipient.subscriber
        if subscriber is None or subscriber.status != 'subscribed':
            recipient.error = 'no longer subscribed'
            db.session.commit()
            continue
        try:
            subject, text, html = compose_email(content, org, subscriber,
                                                fields=rendered_fields,
                                                logo=logo)
            send_email(subscriber.email, subject, text, html=html,
                       attribution=False)
            recipient.sent_at = utcnow()
        except Exception as e:      # noqa: BLE001 -- one bad address must not stop the batch
            recipient.error = str(e)[:500]
            log.error('newsletter_recipient_failed',
                      delivery_id=delivery.id, error=str(e))
        db.session.commit()         # progress survives a crash

    remaining = DeliveryRecipient.query.filter_by(
        delivery_id=delivery.id, sent_at=None, error=None).count()
    delivery.sent_count = DeliveryRecipient.query.filter(
        DeliveryRecipient.delivery_id == delivery.id,
        DeliveryRecipient.sent_at.isnot(None)).count()
    delivery.failed_count = DeliveryRecipient.query.filter(
        DeliveryRecipient.delivery_id == delivery.id,
        DeliveryRecipient.error.isnot(None)).count()
    if remaining:
        db.session.commit()
        from app.platform.jobs import enqueue
        enqueue('newsletter.send_delivery', org_id=delivery.org_id,
                delivery_id=delivery.id)
    else:
        delivery.status = 'done'
        delivery.finished_at = utcnow()
        db.session.commit()
        log.info('newsletter_delivery_done', delivery_id=delivery.id,
                 sent=delivery.sent_count, failed=delivery.failed_count)


@job('newsletter.confirmation_email')
def send_confirmation(payload: dict) -> None:
    from app.models import Organization
    from app.models.newsletter import Subscriber
    from app.platform.mailer import try_send_email
    from app.platform.tenant import org_url

    subscriber = db.session.get(Subscriber, payload.get('subscriber_id'))
    if subscriber is None or subscriber.status != 'pending':
        return
    org = db.session.get(Organization, subscriber.org_id)
    confirm_url = org_url(org, f'/subscribe/confirm/{subscriber.token}')
    from app.platform.emails import render_message
    subject = t('newsletter.confirm_subject', org=org.site_name)
    text, html = render_message(
        'emails/confirm_subscription.html', subject=subject, org=org,
        text=(f'{subject}:\n\n{confirm_url}\n\n'
              f'{t("newsletter.confirm_ignore")}'),
        heading=t('newsletter.confirm_heading', org=org.site_name),
        intro=t('newsletter.confirm_intro', org=org.site_name),
        action=t('newsletter.confirm_action'), action_url=confirm_url,
        outro=t('newsletter.confirm_ignore'))
    try_send_email(subscriber.email, subject, text, html=html,
                   attribution=False)
