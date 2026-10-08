#!/bin/sh
# A fake image handler: records which one ran and with what.
name=$1; shift
echo "$name $*" >> "$ORACLE_OUT/opened.log"
