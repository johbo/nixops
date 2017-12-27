{ nixos ? <nixpkgs/nixos>
, system ? builtins.currentSystem
}:

let
  machine-configuration = import ./nix/virtualbox-image-nixops.nix;

  machine = import nixos {
    inherit system;
    configuration = machine-configuration;
  };

  nixosVersion = machine.config.system.nixosVersion;
  imageName = "virtualbox-nixops-${nixosVersion}.vmdk";

  pkgs = import <nixpkgs> { };

in rec {
  ova = machine.config.system.build.virtualBoxOVA;

  baseImage = pkgs.stdenv.mkDerivation rec {
    name = "virtualbox-nixops-image-${version}";
    version = nixosVersion;
    phases = [ "installPhase" ];
    nativeBuildInputs = [
      ova
    ];
    installPhase = ''
      mkdir ova
      tar -xf ${ova}/*.ova -C ova
      mv ova/{nixos*,nixos}.vmdk

      mkdir -p $out
      name=$out/${imageName}
      cp ./ova/nixos.vmdk $name
      sha256sum $name > $name.sha256
    '';
  };

  baseImageArchive = pkgs.stdenv.mkDerivation rec {
    name = "virtualbox-nixops-image-archive-${version}";
    version = nixosVersion;
    phases = [ "installPhase" ];
    nativeBuildInputs = [
      baseImage
    ];
    installPhase = ''
      mkdir -p $out
      name=$out/${imageName}.xz
      xz < ${baseImage}/${imageName} > $name
      sha256sum $name > $name.sha256
    '';
  };
}
