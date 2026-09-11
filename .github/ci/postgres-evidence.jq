{
  image: $image,
  manifest_digest: $manifest,
  media_type: .mediaType,
  platforms: [
    .manifests[]
    | select(
        .platform.os == "linux"
        and (.platform.architecture == "amd64" or .platform.architecture == "arm64")
      )
    | {
        os: .platform.os,
        architecture: .platform.architecture,
        digest: .digest
      }
  ]
}
