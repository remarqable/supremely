# Email

Email is optional. Supremely installs, signs people in, publishes, and
onboards members with no email service configured at all. What you lose
without it is the part that has to leave the building: newsletters,
notification email, and the self-service password reset. Recovery without
email is a command (`flask users reset-password EMAIL`) or a platform
administrator.

When you do want it, go to **Administration - Email** and pick a provider
from the list beside it. Each has its own page asking only for what it
needs. All three end up sending your mail; the choice is about how much you
have to know, and how much you intend to send.

| | [Custom SMTP](#custom-smtp) | [Gmail](#gmail) | [Mailgun](#mailgun) |
|---|---|---|---|
| You provide | Host, port, credentials | A Google app password | An API key and a domain |
| Sends over | SMTP | SMTP | Mailgun's HTTP API |
| Good for | A provider you already run or pay for | A small organization, low volume | Newsletters to a real list |

Whichever you pick, the **From address** is what recipients see, and the
**Send test email** button below the form is the fastest way to find out
whether it works. Send yourself one before you send a newsletter to anybody
else.

## Gmail

Good for a small organization that already has Google Workspace or a Gmail
account. Google will not accept your ordinary password here, so this takes
one step in your Google account first.

1. Open [Google Account security](https://myaccount.google.com/security) and
   turn on **2-Step Verification** if it is not on already. App passwords do
   not exist without it.
2. Go to [App passwords](https://myaccount.google.com/apppasswords), name the
   app something you will recognise later (`Supremely`), and create it.
   Google shows you a 16-character password once. Copy it.
3. In Supremely, open **Administration - Email - Gmail** and fill in:
   - **SMTP username**: the full Gmail address, for example
     `you@example.com`.
   - **SMTP password**: the app password from step 2, not your account
     password.
   - **From address**: usually the same address. Gmail rewrites this to the
     account's own address anyway unless you have configured a verified
     alias.
4. Press **Save and use Gmail**, then send yourself a test email. The test
   form appears once a provider is the one in use, so it always tests what
   would actually send.

The server, port and encryption are filled in for you (`smtp.gmail.com`,
port 587, STARTTLS). If you would rather set them yourself, Custom SMTP
takes the same values.

**Sending limits.** A personal Gmail account will stop accepting mail after
roughly 500 recipients a day, and Workspace accounts after around 2,000.
Those are per day, not per message, and a newsletter counts one against the
limit for every subscriber. Google does not warn you politely when you cross
it. If your list is larger than a few hundred people, use Mailgun.

## Mailgun

Built for sending to a list, and the right choice once a newsletter matters.
Supremely talks to Mailgun's HTTP API rather than its SMTP relay.

1. In the Mailgun dashboard, add and verify your sending domain, under
   **Sending - Domains**. Verification means adding the DNS records Mailgun
   shows you, and it is the part that takes the longest, because DNS changes
   take time to propagate. A subdomain such as `mg.example.com` is the usual
   choice and keeps your main domain's DNS alone.
2. Note which region the domain is in. Mailgun runs the United States and
   Europe as separate services with separate dashboards, and a domain
   belongs to exactly one of them. A domain created in Europe cannot be
   reached through the US region, and the error message when you get this
   wrong says the domain does not exist, not that the region is wrong.
3. Get a sending API key from **Sending - Domain settings - Sending keys**,
   or an account API key from your account settings.
4. In Supremely, open **Administration - Email - Mailgun** and fill in:
   - **API key**: from step 3.
   - **Sending domain**: exactly the domain you verified, for example
     `mg.example.com`. Not a URL, and not your website's domain unless that
     is what you verified.
   - **Region**: the one from step 2.
   - **From address**: an address at the sending domain, for example
     `news@mg.example.com`.
5. Press **Save and use Mailgun**, then send yourself a test email.

If Mailgun refuses the message, Supremely shows you Mailgun's own words
rather than a generic failure. `Invalid private key` means the API key is
wrong. `Domain not found` usually means the region is wrong, or the domain
is not verified yet.

## Custom SMTP

Any SMTP server. Choose this when you run your own mail server, or when your
provider is not one of the named ones above. You will need the host, the
port, whether it uses STARTTLS, and a username and password.

Most providers use port 587 with STARTTLS on. Port 465 is implicit TLS and
is not what the TLS checkbox means; if your provider offers both, use 587.

## Changing your mind

Whichever page you saved last is the provider in use, and that page says
**In use** at the top. Setting up one does not disturb the others, with one
exception worth knowing: Custom SMTP and Gmail share the username and
password fields, because for both of them that is the same thing, an SMTP
login. Saving one overwrites the other's credentials. Mailgun's key and
domain are its own and are never touched by either.

So you can try Gmail, find the sending limit too low, set up Mailgun, and
your SMTP host is still where you left it. What you would have to retype is
a Custom SMTP username and password, if you had set those up before trying
Gmail. Stored passwords and
API keys are never shown again after saving. Leaving the field blank keeps
the one already stored, so you can edit the domain without retyping the key.

## Where the settings live

Email is configured once for the whole installation, by a platform
administrator, and every organization on it sends through the same provider.
Credentials are stored in the database in plain text, the same as every
other installation setting, so treat database backups accordingly.
