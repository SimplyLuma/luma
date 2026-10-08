import site
import sysconfig

purelib = sysconfig.get_path('purelib', vars=dict(base='/app'))
platlib = sysconfig.get_path('platlib', vars=dict(platbase='/app'))
site.addsitedir(purelib)
if platlib != purelib:
    site.addsitedir(platlib)

