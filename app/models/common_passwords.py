"""The passwords guessed first.

Not a serious wordlist, and not meant to be: a full one belongs in a
service, and checking against a breach corpus needs a network call this
project deliberately does not make. This is the short head of the
distribution, the strings a spraying attack tries before anything else, so
that a length rule alone cannot wave them through.

Compared against the lower-cased password with digits kept, so Password1
and PASSWORD1 both land here. Nothing shorter than the length minimum is
listed, since that rule refuses those already.
"""

COMMON_PASSWORDS = frozenset({
    '12345678', '123456789', '1234567890',
    '987654321', 'aaaaaaaa', 'abc12345', 'abcd1234', 'access14',
    'admin123', 'adobe123', 'amateur1', 'anthony1', 'asdfghjk', 'ashley12',
    'asshole1', 'azerty12', 'babygirl', 'bailey12', 'baseball', 'basketba',
    'batman12', 'buster12', 'butterfly', 'charlie1', 'cheese12', 'chelsea1',
    'chicken1', 'computer', 'cookie12', 'corvette', 'cowboys1', 'dallas12',
    'daniel12', 'dragon12', 'edward12', 'eeyore12', 'elephant', 'fireball',
    'flower12', 'football', 'freedom1', 'ginger12', 'gateway1', 'gundam12',
    'hannah12', 'hardcore', 'harley12', 'hello123', 'hunter12', 'iceman12',
    'iloveyou', 'internet', 'jamesbond', 'jasmine1', 'jennifer', 'jessica1',
    'jonathan', 'jordan12', 'joshua12', 'junior12', 'killer12', 'knight12',
    'letmein1', 'liverpool', 'london12', 'lovely12', 'madison1', 'maggie12',
    'manager1', 'marlboro', 'matthew1', 'maverick', 'melissa1', 'michael1',
    'michelle', 'midnight', 'monkey12', 'mustang1', 'nascar12', 'nicholas',
    'nicole12', 'ninja123', 'oliver12', 'orange12', 'pass1234', 'passw0rd',
    'password', 'password1', 'password12', 'password123', 'patrick1',
    'peanut12', 'pepper12', 'phoenix1', 'please12', 'pokemon1', 'porsche1',
    'princess', 'purple12', 'q1w2e3r4', 'qazwsxedc', 'qwerty12', 'qwerty123',
    'qwertyui', 'rainbow1', 'ranger12', 'rangers1', 'redskins', 'richard1',
    'robert12', 'rush2112', 'samantha', 'samsung1', 'scooter1', 'secret12',
    'security', 'shadow12', 'silver12', 'slipknot', 'soccer12', 'sparky12',
    'spider12', 'starwars', 'steelers', 'summer12', 'sunshine', 'superman',
    'test1234', 'thomas12', 'thunder1', 'tigger12', 'trustno1', 'trouble1',
    'victoria', 'welcome1', 'whatever', 'william1', 'winner12', 'winston1',
    'yankees1', 'zaq12wsx', 'zxcvbnm1', 'zxcvbnma',
})
