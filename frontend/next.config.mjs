/** Konva ships a Node entry that imports the native `canvas` package. The editor canvas is client-only (dynamic import, ssr:false), so it is stubbed out. */
export default {
  reactStrictMode: true,
  webpack: (config) => { config.resolve.alias = { ...config.resolve.alias, canvas: false }; config.externals = [...(config.externals ?? []), { canvas: "commonjs canvas" }]; return config; },
};
