// Firefox's promise-returning API is `browser`; Chrome has long offered only `chrome`.
export const ext: typeof chrome = (globalThis as { browser?: typeof chrome }).browser ?? chrome
