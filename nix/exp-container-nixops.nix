
{ config, options, pkgs, lib, ... }:

# Maps the inline declarations of a container into the namespace
# config.deployment.container for a consistent usage inside of
# nixops.

with lib;

let
  cfg = config.container;

in {
  imports = [
    ./exp-container-inline-declaration.nix
  ];

  options = {
    deployment.container = options.container;
  };

  config = {
    deployment.container = cfg;
  };
}
