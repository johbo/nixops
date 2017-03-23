
{ config, options, pkgs, lib, ... }:

# Map the features of declarative containers into the container
# configuration, so that they can be used for imperative
# containers as well.

with lib;

let
  cfg = config.container;

  bindMountOpts = { name, config, ... }: {

    options = {
      mountPoint = mkOption {
        example = "/mnt/usb";
        type = types.str;
        description = "Mount point on the container file system.";
      };
      hostPath = mkOption {
        default = null;
        example = "/home/alice";
        type = types.nullOr types.str;
        description = "Location of the host path to be mounted.";
      };
      isReadOnly = mkOption {
        default = true;
        example = true;
        type = types.bool;
        description = "Determine whether the mounted path will be accessed in read-only mode.";
      };
    };

    config = {
      mountPoint = mkDefault name;
    };

  };

in {
  options.container =
  # Grab nearly everything from "containers.<name>"
  (removeAttrs
    (options.containers.type.getSubOptions "containers")
    [ "config" "path" "_module" ]) //
  {
    writeContainerConfig = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Write declarative container configuration into the system
        derivation.
      '';
    };
  };

  config = mkIf cfg.writeContainerConfig {
    system.build.containerConf = pkgs.writeText "container-conf" (
      let
        mkPortStr = p: p.protocol + ":" + (toString p.hostPort) + ":" + (if p.containerPort == null then toString p.hostPort else toString p.containerPort);
        mkBindFlag = d:
          let flagPrefix = if d.isReadOnly then " --bind-ro=" else " --bind=";
              mountstr = if d.hostPath != null then "${d.hostPath}:${d.mountPoint}" else "${d.mountPoint}";
          in flagPrefix + mountstr ;
        mkBindFlags = bs: concatMapStrings mkBindFlag (lib.attrValues bs);
      in ''
        ${optionalString cfg.privateNetwork ''
          PRIVATE_NETWORK=1
          ${optionalString (cfg.hostBridge != null) ''
            HOST_BRIDGE=${cfg.hostBridge}
          ''}
          ${optionalString (length cfg.forwardPorts > 0) ''
            HOST_PORT=${concatStringsSep "," (map mkPortStr cfg.forwardPorts)}
          ''}
          ${optionalString (cfg.hostAddress != null) ''
            HOST_ADDRESS=${cfg.hostAddress}
          ''}
          ${optionalString (cfg.hostAddress6 != null) ''
            HOST_ADDRESS6=${cfg.hostAddress6}
          ''}
          ${optionalString (cfg.localAddress != null) ''
            LOCAL_ADDRESS=${cfg.localAddress}
          ''}
          ${optionalString (cfg.localAddress6 != null) ''
            LOCAL_ADDRESS6=${cfg.localAddress6}
          ''}
        ''}
        INTERFACES="${toString cfg.interfaces}"
        MACVLANS="${toString cfg.macvlans}"
        ${optionalString cfg.autoStart ''
          AUTO_START=1
        ''}
        EXTRA_NSPAWN_FLAGS="${mkBindFlags cfg.bindMounts}"
      '');

    system.extraSystemBuilderCmds = ''
      mkdir -p $out/container

      ln -s ${config.system.build.containerConf} $out/container/container.conf
    '';
  };
}
