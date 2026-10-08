# SPDX-License-Identifier: Apache-2.0
# Homebrew's commands on the search path of every shell, after the system's
# own so a formula never replaces a system tool. Present before Homebrew is
# set up, so the first thing installed works without a new terminal.
if [ -z "${LUMA_HOMEBREW_PROFILE:-}" ]; then
  LUMA_HOMEBREW_PROFILE=1
  HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew
  HOMEBREW_CELLAR="$HOMEBREW_PREFIX/Cellar"
  HOMEBREW_REPOSITORY="$HOMEBREW_PREFIX/Homebrew"
  : "${HOMEBREW_NO_ANALYTICS:=1}"
  export HOMEBREW_PREFIX HOMEBREW_CELLAR HOMEBREW_REPOSITORY HOMEBREW_NO_ANALYTICS
  case ":${PATH}:" in
    *":$HOMEBREW_PREFIX/bin:"*) ;;
    *) PATH="${PATH:+$PATH:}$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin" ;;
  esac
  case ":${MANPATH:-}:" in
    *":$HOMEBREW_PREFIX/share/man:"*) ;;
    *) MANPATH="${MANPATH:-}:$HOMEBREW_PREFIX/share/man" ;;
  esac
  case ":${INFOPATH:-}:" in
    *":$HOMEBREW_PREFIX/share/info:"*) ;;
    *) INFOPATH="$HOMEBREW_PREFIX/share/info:${INFOPATH:-}" ;;
  esac
  export PATH MANPATH INFOPATH
  if [ -n "${BASH_VERSION:-}" ] && [ -r "$HOMEBREW_PREFIX/etc/profile.d/bash_completion.sh" ]; then
    . "$HOMEBREW_PREFIX/etc/profile.d/bash_completion.sh"
  fi
fi
