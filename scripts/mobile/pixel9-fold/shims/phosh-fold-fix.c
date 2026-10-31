/*
 * LD_PRELOAD shim for phosh 0.55 on the Fold: registers the layer-surface
 * "wl-output" property with G_PARAM_CONSTRUCT, the runtime equivalent of
 * 0001-layer-surface-set-wl-output-at-construction.patch, until the fix ships
 * in luma-phosh.
 *
 * clang --target=aarch64-linux-gnu -shared -fPIC -nostdlib -O2 \
 *   -mbranch-protection=standard -fuse-ld=lld -o luma-fold-phosh-fix.so this.c
 */
#define RTLD_NEXT ((void *) -1l)
#define G_PARAM_CONSTRUCT (1 << 2)
#define G_PARAM_CONSTRUCT_ONLY (1 << 3)

extern void *dlsym (void *handle, const char *symbol);

typedef void *(*spec_pointer_fn) (const char *, const char *, const char *, int);

static int
str_eq (const char *s, const char *w)
{
  while (*s && *s == *w) {
    s++;
    w++;
  }
  return *s == '\0' && *w == '\0';
}

void *
g_param_spec_pointer (const char *name, const char *nick, const char *blurb, int flags)
{
  static spec_pointer_fn real;

  if (!real)
    real = (spec_pointer_fn) dlsym (RTLD_NEXT, "g_param_spec_pointer");
  /* Only PhoshLayerSurface's property; PhoshMonitor's is already construct-only */
  if (name && blurb && !(flags & G_PARAM_CONSTRUCT_ONLY) && str_eq (name, "wl-output") &&
      str_eq (blurb, "The wl_output associated with this surface"))
    flags |= G_PARAM_CONSTRUCT;
  return real (name, nick, blurb, flags);
}

/*
 * phosh's rotation manager forces the built-in panel to an even transform
 * while locked and to "normal" on unlock when it saw no lock first (the
 * session starts locked). The Fold's inner panel is upright at transform 270,
 * so both rotate it sideways and break the touch calibration. Without an
 * accelerometer there is nothing to fix up: make fixup_lockscreen_orientation()
 * return at its built-in check. Patches only this exact phosh build.
 */
#define PROT_READ  0x1
#define PROT_WRITE 0x2
#define PROT_EXEC  0x4
#define PROT_BTI   0x10

struct dl_phdr_info {
  unsigned long dlpi_addr;
  const char *dlpi_name;
  /* remaining fields unused */
};

extern int dl_iterate_phdr (int (*cb) (struct dl_phdr_info *, unsigned long, void *), void *data);
extern int mprotect (void *addr, unsigned long len, int prot);
extern int unsetenv (const char *name);
extern char *program_invocation_name;

/* Whether the running program is phosh itself ("phosh" or ".../phosh") */
static int
is_phosh (void)
{
  const char *p = program_invocation_name, *base = p;

  if (!p)
    return 0;
  for (; *p; p++)
    if (*p == '/')
      base = p + 1;
  return str_eq (base, "phosh");
}

/*
 * Known luma-phosh builds: the built-in check is `bl is_builtin` followed by
 * `cbz w0, <return>`; both builds branch 0x70 ahead, so `b <return>` is the
 * same word.
 */
static const struct {
  unsigned long check;		/* address of the cbz */
  unsigned int bl;		/* the bl right before it */
} fixup_builds[] = {
  { 0xda1a0, 0x97fee58du },	/* luma-phosh 0.55.0-1.luma.71 */
  { 0xdc4e0, 0x97fef994u },	/* luma-phosh 0.55.0-1.luma.73 */
};
#define INSN_CBZ_RETURN     0x34000380u
#define INSN_B_RETURN       0x1400001cu

static int
main_program_base (struct dl_phdr_info *info, unsigned long size, void *data)
{
  (void) size;
  *(unsigned long *) data = info->dlpi_addr;
  return 1;			/* the first entry is the main program */
}

__attribute__((constructor)) static void
disable_lockscreen_orientation_fixup (void)
{
  unsigned long base = 0, page;
  volatile unsigned int *insn = 0;
  unsigned int i;

  /* phosh spawns helpers (glycin's bwrap loaders, apps) that inherit the
   * environment; keep the shim out of them */
  if (!is_phosh ())
    return;
  unsetenv ("LD_PRELOAD");

  dl_iterate_phdr (main_program_base, &base);
  if (!base)
    return;
  for (i = 0; i < sizeof (fixup_builds) / sizeof (fixup_builds[0]); i++) {
    volatile unsigned int *p = (volatile unsigned int *) (base + fixup_builds[i].check);

    if (p[-1] == fixup_builds[i].bl && p[0] == INSN_CBZ_RETURN) {
      insn = p;
      break;
    }
  }
  if (!insn)
    return;

  page = (unsigned long) insn & ~0xfffUL;
  if (mprotect ((void *) page, 0x1000, PROT_READ | PROT_WRITE))
    return;
  insn[0] = INSN_B_RETURN;
  __asm__ volatile ("dc cvau, %0\n\tdsb ish\n\tic ivau, %0\n\tdsb ish\n\tisb"
                    :: "r" (insn) : "memory");
  /* PROT_BTI is rejected when the kernel lacks BTI support */
  if (mprotect ((void *) page, 0x1000, PROT_READ | PROT_EXEC | PROT_BTI))
    mprotect ((void *) page, 0x1000, PROT_READ | PROT_EXEC);
}
