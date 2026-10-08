# Native Android task removal

The Apache-2.0 patch registers Android's existing `TaskStackListener` in
WayDroidService. Only authoritative `onTaskRemoved(taskId)` dispatches the
backward-compatible window HAL 1.3 method. It does not infer task removal from
surface absence or timers, and does not remove an Android task recursively.

The matching hardware patch retains HAL 1.0–1.2 and accepts the new operation
only from system_server UID 1000. Callback retirement precedes collection erase;
in-flight native listeners retain strong ownership. The exact HIDL definitions,
Java/native dependencies and VINTF manifest belong to the matching image pair.
