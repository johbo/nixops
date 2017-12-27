{ nixos ? <nixpkgs/nixos>
, system ? builtins.currentSystem
}:

let
  machine-configuration = import ./nix/virtualbox-image-nixops.nix;

  machine = import nixos {
    inherit system;
    configuration = machine-configuration;
  };

  pkgs = import <nixpkgs> { };

in rec {
  ova = machine.config.system.build.virtualBoxOVA;

  nixos-disk = pkgs.stdenv.mkDerivation rec {
    name = "virtualbox-nixops-image-${version}";
    version = machine.config.system.nixosVersion;
    phases = [ "installPhase" ];
    nativeBuildInputs = [
      ova
    ];
    installPhase = ''
      mkdir ova
      tar -xf ${ova}/*.ova -C ova
      mv ova/{nixos*,nixos}.vmdk

      mkdir -p $out
      name=$out/virtualbox-nixops-${version}.vmdk.xz
      xz < ./ova/nixos.vmdk > $name
      sha256sum $name > $name.sha256
    '';
  };
}
