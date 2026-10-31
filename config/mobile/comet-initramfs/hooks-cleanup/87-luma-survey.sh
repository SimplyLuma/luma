#!/bin/sh
# Fold basics survey: with luma.fold_survey=1, install a oneshot that logs
# batteries, USB, backlight, sensors, input, thermal, RTC and one suspend to
# the journal ("luma-survey:"); otherwise remove it again. The unit also
# guards itself on the flag, since /etc persists across RAM boots.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_survey=1" /proc/cmdline; then
	install -m 755 /luma-fold/survey/luma-survey.sh $s/luma-survey.sh
	cat > $s/luma-survey.service <<"UNIT"
[Unit]
Description=Fold basics survey
ConditionKernelCommandLine=luma.fold_survey=1
[Service]
Type=oneshot
ExecStart=/bin/sh /etc/systemd/system/luma-survey.sh
SyslogIdentifier=luma-survey
TimeoutStartSec=280
[Install]
WantedBy=multi-user.target
UNIT
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-survey.service $s/multi-user.target.wants/luma-survey.service
else
	rm -f $s/luma-survey.sh $s/luma-survey.service $s/multi-user.target.wants/luma-survey.service
fi
exit 0
