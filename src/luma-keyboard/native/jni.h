/* Minimal JNI type surface for LatinIME's HOST_TOOL build.
 * Luma's decoder calls the C++ core directly and never loads a JVM. */
#ifndef LUMA_LATINIME_HOST_JNI_H
#define LUMA_LATINIME_HOST_JNI_H

#include <cstdint>

typedef std::int8_t jbyte;
typedef std::uint8_t jboolean;
typedef std::int16_t jshort;
typedef std::uint16_t jchar;
typedef std::int32_t jint;
typedef std::int64_t jlong;
typedef float jfloat;
typedef double jdouble;
typedef jint jsize;
typedef void *jobject;
typedef jobject jclass;
typedef jobject jstring;
typedef jobject jarray;
typedef jarray jintArray;
typedef jarray jfloatArray;
typedef jarray jbooleanArray;
typedef jarray jobjectArray;
struct JNIEnv;

#define JNI_TRUE 1
#define JNI_FALSE 0

#endif
