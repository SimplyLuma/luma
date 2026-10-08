# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only OBJ/STL geometry. No materials or external resources load."""
from dataclasses import dataclass
from pathlib import Path
import math
import struct

MAX_BYTES = 32 * 1024 * 1024
MAX_VERTICES = 120_000
MAX_TRIANGLES = 40_000


@dataclass(frozen=True)
class Mesh:
    triangles: tuple
    dimensions: tuple


def _point(values):
    point = tuple(float(value) for value in values)
    if len(point) != 3 or not all(math.isfinite(value) for value in point):
        raise ValueError('The model contains invalid coordinates.')
    return point


def load_mesh(path: Path) -> Mesh:
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('This model exceeds the 32 MB preview limit.')
    triangles = []
    def append(points):
        if len(triangles) >= MAX_TRIANGLES:
            raise ValueError('This model exceeds the 40,000 triangle preview limit.')
        triangles.append(tuple(points))
    if path.suffix.lower() == '.obj':
        vertices = []
        for line in data.decode('utf-8', errors='strict').splitlines():
            fields = line.split('#', 1)[0].split()
            if not fields:
                continue
            if fields[0] == 'v':
                if len(vertices) >= MAX_VERTICES:
                    raise ValueError('This model has too many vertices to preview.')
                vertices.append(_point(fields[1:4]))
            elif fields[0] == 'f':
                if len(fields) < 4 or len(fields) > 1025:
                    raise ValueError('The model contains an invalid face.')
                face = []
                for field in fields[1:]:
                    index = int(field.split('/', 1)[0])
                    if index == 0 or not -len(vertices) <= index <= len(vertices):
                        raise ValueError('The model refers to a missing vertex.')
                    face.append(vertices[index - 1 if index > 0 else index])
                for index in range(1, len(face) - 1):
                    append((face[0], face[index], face[index + 1]))
    elif path.suffix.lower() == '.stl':
        count = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else -1
        if count >= 0 and len(data) == 84 + 50 * count:
            if count > MAX_TRIANGLES:
                raise ValueError('This model exceeds the 40,000 triangle preview limit.')
            for offset in range(84, len(data), 50):
                values = struct.unpack_from('<12fH', data, offset)
                append(tuple(_point(values[start:start + 3]) for start in (3, 6, 9)))
        else:
            points = []
            for line in data.decode('ascii', errors='strict').splitlines():
                fields = line.split()
                if fields and fields[0].lower() == 'vertex':
                    points.append(_point(fields[1:]))
                    if len(points) == 3:
                        append(points)
                        points = []
            if points:
                raise ValueError('The model contains an incomplete triangle.')
    else:
        raise ValueError('Viewer previews OBJ and STL models.')
    if not triangles:
        raise ValueError('The model contains no surfaces to preview.')
    lower = tuple(min(point[axis] for triangle in triangles for point in triangle) for axis in range(3))
    upper = tuple(max(point[axis] for triangle in triangles for point in triangle) for axis in range(3))
    dimensions = tuple(high - low for low, high in zip(lower, upper))
    extent = math.hypot(*dimensions)
    if not math.isfinite(extent) or extent <= 0:
        raise ValueError('The model has no measurable extent.')
    # Subtraction before addition avoids overflowing the midpoint for large coordinates.
    center = tuple(low + span / 2 for low, span in zip(lower, dimensions))
    normalized = tuple(tuple(tuple((value - center[axis]) / extent for axis, value in enumerate(point))
                             for point in triangle) for triangle in triangles)
    return Mesh(normalized, dimensions)


def project(mesh: Mesh, yaw: float, pitch: float):
    """Rotate normalized triangles and sort back to front for an orthographic view."""
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    result = []
    for triangle in mesh.triangles:
        rotated = []
        for x, y, z in triangle:
            x, z = cy * x + sy * z, -sy * x + cy * z
            rotated.append((x, cp * y - sp * z, sp * y + cp * z))
        a, b, c = rotated
        u = tuple(b[i] - a[i] for i in range(3))
        v = tuple(c[i] - a[i] for i in range(3))
        normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        length = math.sqrt(sum(value * value for value in normal))
        light = .35 + .65 * abs(normal[2]) / length if length else .35
        result.append((sum(point[2] for point in rotated), tuple(rotated), light))
    return sorted(result, key=lambda face: face[0])
