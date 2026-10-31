/*
 * LD_PRELOAD shim for phoc 0.55 / wlroots 0.20 on the Fold: wlroots does not
 * copy libinput's WL_OUTPUT into wlr_touch.output_name, so phoc maps every
 * touchscreen to the built-in output. Fill in output_name from
 * LUMA_TOUCH_OUTPUTS ("<device-name substring>:<output>,...") the first time
 * phoc looks at a touch device.
 *
 * clang --target=aarch64-linux-gnu -shared -fPIC -nostdlib -O2 \
 *   -mbranch-protection=standard -fuse-ld=lld -o luma-fold-phoc-touch.so this.c
 */
#define RTLD_NEXT ((void *) -1l)

/* wlroots 0.20 layouts (offsets checked against phoc's own loads) */
#define INPUT_DEVICE_NAME_OFFSET 0x08	/* struct wlr_input_device.name */
#define TOUCH_OUTPUT_NAME_OFFSET 0x30	/* struct wlr_touch.output_name */

extern void *dlsym (void *handle, const char *symbol);
extern char *getenv (const char *name);
extern char *strndup (const char *s, unsigned long n);

typedef void *(*touch_from_fn) (void *);

/* Whether the substring s[0..n) occurs in hay */
static int
contains (const char *hay, const char *s, unsigned long n)
{
  for (; *hay; hay++) {
    unsigned long i = 0;

    while (i < n && hay[i] && hay[i] == s[i])
      i++;
    if (i == n)
      return 1;
  }
  return 0;
}

static char *
lookup_output (const char *device_name)
{
  const char *map = getenv ("LUMA_TOUCH_OUTPUTS");

  while (map && *map) {
    const char *colon = map, *end;

    while (*colon && *colon != ':' && *colon != ',')
      colon++;
    if (*colon != ':')
      return 0;
    end = colon + 1;
    while (*end && *end != ',')
      end++;
    if (contains (device_name, map, colon - map))
      return strndup (colon + 1, end - colon - 1);
    map = *end ? end + 1 : end;
  }
  return 0;
}

void *
wlr_touch_from_input_device (void *device)
{
  static touch_from_fn real;
  void *touch;
  char **output_name;
  const char *device_name;

  if (!real)
    real = (touch_from_fn) dlsym (RTLD_NEXT, "wlr_touch_from_input_device");
  touch = real (device);

  output_name = (char **) ((char *) touch + TOUCH_OUTPUT_NAME_OFFSET);
  device_name = *(const char **) ((char *) device + INPUT_DEVICE_NAME_OFFSET);
  if (!*output_name && device_name)
    *output_name = lookup_output (device_name);
  return touch;
}
