"""What Luma Connect says about an account, without any toolkit.

Kept apart from the window so the words and the change detection can be
tested where no display exists.
"""
from gi.repository import Gio,GLib

# Fields that change on every refresh without anything visible changing.
VOLATILE=frozenset({'generation','observed_at','valid_until','lease_deadline_monotonic'})


def _uses_24_hour():
    try:
        source=Gio.SettingsSchemaSource.get_default()
        schema=source.lookup('org.gnome.desktop.interface',True) if source else None
        if schema and schema.has_key('clock-format'):
            return Gio.Settings.new('org.gnome.desktop.interface').get_string('clock-format')=='24h'
    except GLib.Error:
        pass
    return False

def when_text(value,*,prefix=''):
    """'Today, 8:45 PM' / 'Yesterday, 09:10' / 'Sep 3, 8:45 PM'."""
    if not isinstance(value,(int,float)) or isinstance(value,bool) or value<=0:return 'Not yet'
    moment=GLib.DateTime.new_from_unix_local(int(value))
    if moment is None:return 'Not yet'
    now=GLib.DateTime.new_now_local()
    clock=moment.format('%H:%M' if _uses_24_hour() else '%-l:%M %p')
    days=(GLib.DateTime.new_local(now.get_year(),now.get_month(),now.get_day_of_month(),0,0,0).to_unix()
          -GLib.DateTime.new_local(moment.get_year(),moment.get_month(),moment.get_day_of_month(),0,0,0).to_unix())//86400
    if days==0:return f'{prefix}{clock}' if prefix else f'Today, {clock}'
    day='Yesterday' if days==1 else moment.format('%b %-d' if moment.get_year()==now.get_year() else '%b %-d, %Y')
    return f'{prefix}{day}, {clock}'

COUNT_WORDS={'notes':('note','notes'),'contacts':('contact','contacts'),'photos':('photo','photos'),
    'world-clocks':('city','cities'),'weather-places':('place','places'),'tide-sources':('server','servers'),
    'calendar':('calendar','calendars'),'leaf-books':('book','books'),'messages':('message','messages'),'calls':('call','calls')}

def service_subtitle(item):
    if not item.get('enabled'):return 'Off'
    parts=[]
    if item.get('last_success_at'):parts.append(when_text(item['last_success_at'],prefix='Synced '))
    else:parts.append('Waiting for the first sync')
    count=item.get('item_count')
    if isinstance(count,int) and count>0 and item['id'] in COUNT_WORDS and item['id']!='calendar':
        single,plural=COUNT_WORDS[item['id']];parts.append(f'{count} {single if count==1 else plural}')
    return ' · '.join(parts)

def stable(value):
    """A value with its per-refresh bookkeeping removed, for 'did anything visible change?'."""
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in VOLATILE}
    if isinstance(value,list):return [stable(v) for v in value]
    return value

def device_icon(name):
    lowered=(name or '').casefold()
    return 'luma-connect-phone-symbolic' if any(word in lowered for word in ('phone','pixel','fairphone','galaxy','android','fp6')) \
        else 'luma-connect-laptop-symbolic'

# ── The account profile ─────────────────────────────────────────────────────
# The hub decides what is verified. These words only ever repeat that, so an
# address or number a person typed never reads as verified here.

def format_phone(value):
    """'+14155550199' reads as '+1 415-555-0199'; other countries are shown as stored."""
    text=value if isinstance(value,str) else ''
    digits=text[2:]
    if text.startswith('+1') and len(digits)==10 and digits.isdigit():
        return f'+1 {digits[:3]}-{digits[3:6]}-{digits[6:]}'
    return text

def provider_name(profile):
    return ((profile or {}).get('sign_in') or {}).get('name') or 'your sign-in provider'

def profile_subtitles(profile):
    """What the Name, Email and Phone Number rows say under their titles."""
    profile=profile or {}
    email=profile.get('email')
    if email:
        email=f"{email} · {'Verified by '+(profile.get('email_verified_by') or provider_name(profile)) if profile.get('email_verified') else 'Not verified'}"
    phone=profile.get('phone')
    if phone:
        phone=f"{format_phone(phone)} · {'Verified' if profile.get('phone_verified') else 'Not verified'}"
    return {'name':profile.get('name') or 'No name','email':email or 'Not added','phone':phone or 'Not added'}

def discoverable_subtitle(profile):
    """The phone discoverability switch says exactly what it does today."""
    profile=profile or {}
    if not profile.get('phone'):return 'Add a phone number first.'
    verifiable=((profile.get('verification') or {}).get('phone'))
    if profile.get('phone_lookup')=='on':return 'People who have your number can find you in Messages.'
    if profile.get('discoverable_by_phone'):
        return 'On. It starts once your number is verified.' if verifiable else 'On. It starts once your number is verified, and Luma can’t verify numbers yet.'
    return 'People who have your number could find you in Messages, once it’s verified.'

def sign_in_subtitle(profile):
    name=provider_name(profile)
    if ((profile or {}).get('sign_in') or {}).get('manage_url'):
        return f'{name} · Your password and two-step verification are managed by {name}'
    return name
