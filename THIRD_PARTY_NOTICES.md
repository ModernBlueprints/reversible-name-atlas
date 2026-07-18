# Third-party notices

Reversible Name Atlas locally packages the following selected assets from
[Palantir Blueprint](https://github.com/palantir/blueprint). They are used as
visual assets only; this project does not redistribute Blueprint's React
runtime or require Node in its installation or judge path.

## `@blueprintjs/core` 6.17.2

- License declared by the package: Apache-2.0
- Registry metadata:
  <https://registry.npmjs.org/@blueprintjs%2fcore/6.17.2>
- Exact npm tarball:
  <https://registry.npmjs.org/@blueprintjs/core/-/core-6.17.2.tgz>
- Registry SHA-1: `12b1d6c1a3966faf2def2e31e491de2bfa276774`
- Registry SRI:
  `sha512-mY7gmb31iN80/0wJvLvVpp0RPlYrSX7VNh657cGUDVohUsZ+Nxtj62GgJlNmJhkoura9J2lK3rbvHnBqZcMIIA==`
- Downloaded tarball SHA-256:
  `df7649577a2b7c5548c07538fec57cded14c856b42db0ddeca2d36f315e74180`
- Vendored upstream member: `package/lib/css/blueprint.css`
- Local path: `src/name_atlas/static/vendor/blueprint/blueprint.css`
- Exact local/upstream member SHA-256:
  `04c4dc66a0753f7256194af14f5f96f15a1a149e125898349b26c26c92ba377e`

The compiled stylesheet is copied byte-for-byte. Its `url(...)` declarations
are embedded `data:image/svg+xml` values; it has no CDN or other network asset
dependency.

## `@blueprintjs/icons` 6.13.0

- License declared by the package: Apache-2.0
- Registry metadata:
  <https://registry.npmjs.org/@blueprintjs%2ficons/6.13.0>
- Exact npm tarball:
  <https://registry.npmjs.org/@blueprintjs/icons/-/icons-6.13.0.tgz>
- Registry SHA-1: `7481070b55d0a88f6cc7a4059379ce9723610318`
- Registry SRI:
  `sha512-wEQgADFPwufKiKeF/L5K21bKgouuIbIdYPvMPFv2tt7reSuCEftX0dZpga+21JY4KvEJIz+xtVJoqMmXBesiwQ==`
- Downloaded tarball SHA-256:
  `6344d90154a1d47d62989a90ca9e87d3055963fcd7ce9cd942843eb178b9837d`
- Upstream member pattern:
  `package/lib/esm/generated/20px/paths/{icon}.js`
- Local path pattern:
  `src/name_atlas/static/vendor/blueprint/icons/{icon}.svg`

Only the frozen icon vocabulary is packaged. Each SVG copies the Blueprint
20-pixel path geometry verbatim and adds only an inert SVG wrapper with
`viewBox="0 0 20 20"`, `fill="currentColor"`, `focusable="false"`, and
`aria-hidden="true"`. The icons must accompany visible text in the application.

| Icon | Upstream path-module SHA-256 | Local SVG SHA-256 |
|---|---|---|
| `chevron-right` | `f06ca353d3a2264f8a2260fc5a8b41197f56b1f8254faa55e29440021cfbc198` | `a8767948728333c16fead870a7bb1722c90b7a5c3216fe43319de8c94eb28493` |
| `clipboard` | `40a20491ad645d3c1e1525083270951e7c389b5e987eb5fc9e0c2b2dda4c9fdd` | `8303d5fee96cb8e943043df3ae8fd2cd9183860b55ccf5f5dd1394c8ad06530e` |
| `database` | `c655673b6384af8f67a2be09b0f463dfab441877e377cb42105ee1a290efa101` | `2922db1a1380e56ef312016ea34c256f9faf6afe0bf8a7e489ea5df92ee7e812` |
| `diagram-tree` | `c3312eca12caebab60a2522493a59f7bf9875ab66059227a9ec0bdd68d4b0caa` | `5a1f27fd94f5731aec2e22fe57dedd6cd6a8d686ddf3a9dc7bcfab008b6576a9` |
| `export` | `ef69c5451f2e2131cf094c9401ef4e6532e7fead916b52026abd8df83ab467d0` | `271fcbc30849bca5ebcfd5f92ea33afeefc5398f965cb625b9ab644c14331012` |
| `help` | `8e8562c006b16c08f3e50566bcf90e8a77c8acfefdf2951365d477f6e0a4c68b` | `8ca7d60d8a1031ebd2a03a9d18934c6683fe195c5729f7b5b06c7954bbe58039` |
| `history` | `6422532b1c4607e9152c2ceb3f15e6a8e1f64339a6b4480a575602c345c474cb` | `edfdb3955b3d0ca04f3edff84629e7273e74387b728816dbe4dd0fa1b8e5eb37` |
| `tick-circle` | `3e5534a83c0989cadc285fc4f748a2aecbd33fc29039d90da591c9b0b2681fc9` | `99dfcea0b90e42742afd60f174fc28a18ce94ed5d1ced3ce90568c9e8121166d` |
| `warning-sign` | `d8e4ae34fc1f08e22d56264e5680085e30a7284f97100bfa7e7fb1188d841c66` | `617429b93029fd5411b968df8f836553bdda5ee79649181ebdb2dfa6e17c9f9d` |

## License text

Both exact npm packages contain the same Apache License 2.0 text, SHA-256
`a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9`.
One byte-identical copy is distributed at
`src/name_atlas/static/vendor/blueprint/LICENSE` and in the Python wheel.
The license applies to the Blueprint assets described above, not to replace the
repository's own MIT license.
