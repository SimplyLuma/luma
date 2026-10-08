/* SPDX-License-Identifier: LGPL-2.1-or-later */
// Match the native 142x75 desktop Plymouth/Kiosk lockup. Artwork is supplied
// by the repository's canonical brand SVG at build time, never re-created.
export const startupLockupSvg = (wordmark) => {
    if (!wordmark.includes('viewBox="0 0 2219 715"')) {
        throw new Error("Unsupported canonical Luma wordmark geometry");
    }
    const mark = wordmark.replace('width="2219"', 'width="142"')
            .replace('height="715"', 'height="45"')
            .replace('<svg ', '<svg x="0" y="0" color="#f2f3f4" ');
    const dots = [50, 62, 74, 86].map((x, index) =>
        `<rect x="${x}" y="69" width="6" height="6" fill="#f2f3f4" opacity="${[.4, .7, 1, .7][index]}"/>`).join("");
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 142 75" width="142" height="75">${mark}${dots}</svg>`;
};
