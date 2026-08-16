"""
directives.py - the `<<...>>` convention in Benham's model output.

Benham's replies can carry inline directives the model emits for the code to act
on and then strip, so a nuance costs one API call instead of a round trip:

    <<voice=Bruce>>              switch voice          (voice, archived 2026-08-16)
    <<persona: be more dry>>     lasting personality   (voice, archived 2026-08-16)

Voice is gone and nothing applies a directive any more. This module survives it
because **stripping still matters**: the model was trained on a persona that
describes the syntax, so it can still emit one, and an unstripped `<<...>>`
reaching a friend's DM is a leaked implementation detail. Strip, apply nothing.

Extracted from brain.py when voice was archived. It lived there because voice was
its first consumer, but the guest lane had been calling it since long before -
`guest.py` and `guest_agent.py` both strip every reply - so archiving brain.py
wholesale would have taken the guest lane down with it.

It is deliberately NOT in shared_tools.py: that module is server-side tool
definitions only, and says so.
"""

import re

# Any directive, whatever its name: <<name>>, <<name=value>>, <<name: value>>.
# Name-agnostic on purpose - a directive invented by a future prompt still gets
# stripped rather than shipped to whoever is reading.
_ANY_DIRECTIVE_RE = re.compile(r"<<\s*[A-Za-z_]+\s*(?:[:=][^<>]*)?>>", re.DOTALL)


def strip_directive(text):
    """Remove ALL <<...>> directives, so none reach the person reading."""
    return _ANY_DIRECTIVE_RE.sub("", text or "").strip()
