# -*- coding: utf-8 -*-

import time
from nixops.backends import MachineDefinition, MachineState
from nixops.nix_expr import py2nix
import nixops.util
import nixops.ssh_util
import subprocess


class ContainerDefinition(MachineDefinition):
    """Definition of a NixOS container."""

    @classmethod
    def get_type(cls):
        return "container"

    def __init__(self, xml, config):
        MachineDefinition.__init__(self, xml, config)
        cfg = config["container"]
        self.host = cfg.get("host")
        self.write_container_config = cfg.get('writeContainerConfig', False)


class ContainerState(MachineState):
    """State of a NixOS container."""

    @classmethod
    def get_type(cls):
        return "container"

    state = nixops.util.attr_property("state", MachineState.MISSING, int)  # override
    private_ipv4 = nixops.util.attr_property("privateIpv4", None)
    host = nixops.util.attr_property("container.host", None)
    container_conf = nixops.util.attr_property("container.conf", None)
    client_private_key = nixops.util.attr_property("container.clientPrivateKey", None)
    client_public_key = nixops.util.attr_property("container.clientPublicKey", None)
    public_host_key = nixops.util.attr_property("container.publicHostKey", None)

    def __init__(self, depl, name, id):
        MachineState.__init__(self, depl, name, id)
        self.host_ssh = nixops.ssh_util.SSH(self.logger)
        self.host_ssh.register_host_fun(self.get_host_ssh)
        self.host_ssh.register_flag_fun(self.get_host_ssh_flags)

    @property
    def resource_id(self):
        return self.vm_id

    def address_to(self, m):
        if isinstance(m, ContainerState) and self.host == m.host:
            return m.private_ipv4
        return MachineState.address_to(self, m)

    def get_ssh_name(self):
        assert self.private_ipv4
        if self.host == "localhost":
            return self.private_ipv4
        else:
            return self.get_host_ssh() + "~" + self.private_ipv4

    def get_ssh_private_key_file(self):
        return self._ssh_private_key_file or self.write_ssh_private_key(self.client_private_key)

    def get_ssh_proxy_command(self):
        '''
        When using a remote container host, we have to proxy the ssh connection
        to the container via the host. Connection from host to conatainer is
        established by 'nixos-container run' together with 'nc'. This way we
        can access the containers sshd even if network interfaces are not set
        up or container is not reachable by network from the host.
        '''
        if self.host == 'localhost':
            # TODO: Untested so far.
            cmd_list = [
                'nixos-container', 'run', '{container}', '--',
                'nc', 'localhost', '{container_port}', '2>', '/dev/null',
            ]
        else:
            cmd_list = [
                'ssh', '-x', '-a', 'root@{host}', '{host_flags}',
                'nixos-container', 'run', '{container}', '--',
                'nc', 'localhost', '{container_port}', '2>', '/dev/null',
            ]
        proxy_command = ' '.join(cmd_list).format(
            host=self.get_host_ssh(),
            host_flags=' '.join(self.get_host_ssh_flags()),
            container=self.vm_id,
            container_port=self.ssh_port,
        )
        return proxy_command

    def get_ssh_flags(self, *args, **kwargs):
        flags = super(ContainerState, self).get_ssh_flags(*args, **kwargs)
        flags.extend([
            '-i', self.get_ssh_private_key_file(),
            '-o', 'ProxyCommand={}'.format(self.get_ssh_proxy_command()),
        ])
        return flags

    def get_ssh_for_copy_closure(self):
        # NixOS containers share the Nix store of the host, so we
        # should copy closures to the host.
        return self.host_ssh

    def copy_closure_to(self, path):
        if self.host == "localhost": return
        MachineState.copy_closure_to(self, path)

    def get_host_ssh(self):
        if self.host.startswith("__machine-"):
            m = self.depl.get_machine(self.host[10:])
            if not m.started:
                raise Exception("host machine ‘{0}’ of container ‘{1}’ is not up".format(m.name, self.name))
            return m.get_ssh_name()
        else:
            return self.host

    def get_host_ssh_flags(self):
        if self.host.startswith("__machine-"):
            m = self.depl.get_machine(self.host[10:])
            if not m.started:
                raise Exception("host machine ‘{0}’ of container ‘{1}’ is not up".format(m.name, self.name))
            return m.get_ssh_flags()
        else:
            return []

    def get_unit_property(self, unit, prop):
        """
        Returns the property of a systemd-unit in the container. Returns the
        special value '__error__' if something went wrong e.g. the container
        doesn't exist or is not yet ready to run commands.
        """
        cmd = [
            "nixos-container", "run", "{container}", "--",
            "systemctl", "show", "--property", "{prop}", "--value", "{unit}"
        ]
        cmd = ' '.join(cmd).format(container=self.vm_id, unit=unit, prop=prop)
        try:
            value = self.host_ssh.run_command(
                cmd, logged=True, capture_stdout=True).strip()
        except nixops.ssh_util.SSHCommandFailed as e:
            value = '__error__'
        return value

    def wait_for_ssh(self, check=False):
        # TODO: Add a timeout.
        while self.get_unit_property(
                "sshd.service", "ActiveState") != "active":
            self.log("waiting for containers sshd.service to become active...")
            time.sleep(1)

    # Run a command in the container via ‘nixos-container run’. Since
    # this uses ‘nsenter’, we don't need SSH in the container.
    def run_command(self, command, **kwargs):
        command = command.replace("'", r"'\''")
        return self.host_ssh.run_command(
            "nixos-container run {0} -- bash --login -c 'export HOME=/root; {1}'".format(self.vm_id, command),
            **kwargs)

    def get_physical_spec(self):
        return {('users', 'extraUsers', 'root', 'openssh', 'authorizedKeys', 'keys'): [self.client_public_key]}

    def create_after(self, resources, defn):
        host = defn.host if defn else self.host
        if host and host.startswith("__machine-"):
            return {self.depl.get_machine(host[10:])}
        else:
            return set()

    def create(self, defn, check, allow_reboot, allow_recreate):
        assert isinstance(defn, ContainerDefinition)

        self.set_common_state(defn)

        if not self.client_private_key:
            (self.client_private_key, self.client_public_key) = nixops.util.create_key_pair()

        if self.vm_id is None:
            self.log("building initial configuration...")

            eval_args = self.depl._eval_args(self.depl.nix_exprs)
            eval_args['checkConfigurationOptions'] = False
            expr = " ".join([
                '{ imports = [ <nixops/container-base.nix> ];',
                '  boot.isContainer = true;',
                '  networking.hostName = "{0}";'.format(self.name),
                '  nixpkgs.system = let final = import <nixops/eval-machine-info.nix> {0};'.format(py2nix(eval_args, inline=True)),
                '  in final.resources.machines.{0}.nixpkgs.system;'.format(self.name),
                '  users.extraUsers.root.openssh.authorizedKeys.keys = [ "{0}" ];'.format(self.client_public_key),
                '}'])

            expr_file = self.depl.tempdir + "/{0}-initial.nix".format(self.name)
            nixops.util.write_file(expr_file, expr)

            path = subprocess.check_output(
                ["nix-build", "<nixpkgs/nixos>", "-A", "system",
                 "-I", "nixos-config={0}".format(expr_file)]
                + self.depl._nix_path_flags()).rstrip()

            self.log("creating container...")
            self.host = defn.host
            self.copy_closure_to(path)
            self.vm_id = self.host_ssh.run_command(
                "nixos-container create {0} --ensure-unique-name --system-path '{1}'"
                .format(self.name[:7], path), capture_stdout=True).rstrip()
            self.state = self.STOPPED

        if defn.write_container_config:
            self._write_container_config_if_changed(allow_reboot)

        if self.state == self.STOPPED or check:
            self.host_ssh.run_command("nixos-container start {0}".format(self.vm_id))
            self.state = self.UP

        if self.private_ipv4 is None or check:
            self._read_private_ipv4()

        if self.public_host_key is None:
            self.public_host_key = self.host_ssh.run_command("nixos-container show-host-key {0}".format(self.vm_id), capture_stdout=True).rstrip()
            nixops.known_hosts.add(self.get_ssh_name(), self.public_host_key)

    def _write_container_config_if_changed(self, allow_reboot):
        config_attr = 'nodes."{0}".config.system.build.containerConf'
        config_path = subprocess.check_output(
            ["nix-build"] +
            self.depl._eval_flags(self.depl.nix_exprs) +
            ["-A", config_attr.format(self.name)]).rstrip()
        if self.container_conf == config_path:
            return

        should_restart = self.state in (self.UP, self.STARTING)
        if should_restart and not allow_reboot:
            raise Exception(
                'Restart of the container "{0}" is needed, '
                "run with --allow-reboot.".format(self.name))
        self._write_container_config(config_path)
        if should_restart:
            # TODO: implement a nice restart or make reboot work
            self.stop()
            self.start()
            self.wait_for_ssh()

    def _write_container_config(self, config_path):
        self.log("Updating container configuration")
        self.copy_closure_to(config_path)
        self.host_ssh.run_command("cp {0} /etc/containers/{1}.conf".format(
            config_path, self.vm_id))
        self.container_conf = config_path
        if self.private_ipv4:
            self._read_private_ipv4()

    def _read_private_ipv4(self):
        if self.private_ipv4:
            message = "Changed IPv4 address is {0}"
        else:
            message = "IPv4 address is {0}"
        self.private_ipv4 = self.host_ssh.run_command(
            "nixos-container show-ip {0}".format(self.vm_id),
            capture_stdout=True).rstrip()
        self.log(message.format(self.private_ipv4))

    def destroy(self, wipe=False):
        if not self.vm_id:
            return True

        if not self.depl.logger.confirm(
                "are you sure you want to destroy NixOS container ‘{0}’?"
                .format(self.name)):
            return False

        nixops.known_hosts.remove(self.get_ssh_name(), self.public_host_key)

        self.log_continue("destroying container ...")
        self.host_ssh.run_command(
            "nixos-container destroy {0}".format(self.vm_id))
        self._wait_as_long_as_status("up")
        self.log_end(" destroyed.")

        return True

    def stop(self):
        if not self.vm_id:
            return True
        self.log_continue("stopping container ...")
        self.state = self.STOPPING
        self.host_ssh.run_command(
            "nixos-container stop {0}".format(self.vm_id))
        self._wait_as_long_as_status("up")
        self.log_end(" stopped.")
        self.state = self.STOPPED

    def start(self):
        """
        Starts the container by using 'nixos-container start'. The start
        command may block in case of missing deployment keys on the container.
        Therefore we start the container in parallel with sending the
        deployment keys to it.
        """
        if not self.vm_id:
            return True

        def worker(task):
            if task == "start":
                self.host_ssh.run_command(
                    "nixos-container start {0}".format(self.vm_id))
            elif task == "send_keys":
                self.wait_for_ssh()
                self.send_keys()
            else:
                raise Exception("Unknown task '{}'".format(task))

        self.log("starting container...")
        self.state = self.STARTING
        # FIXME: Produces the following error output:
        # 'mux_client_request_session: read from master failed: Broken pipe'
        nixops.parallel.run_tasks(
            nr_workers=2,
            tasks=["start", "send_keys"],
            worker_fun=worker)

    def _check(self, res):
        if not self.vm_id:
            res.exists = False
            return

        status = self._get_container_status()

        if status == "gone":
            res.exists = False
            self.state = self.MISSING
            return

        res.exists = True

        if status == "down":
            res.is_up = False
            self.state = self.STOPPED
            return

        res.is_up = True
        MachineState._check(self, res)

    def _wait_as_long_as_status(self, status):
        while self._get_container_status() == status:
            time.sleep(1)
            self.log_continue(".")

    def _get_container_status(self):
        return self.host_ssh.run_command(
            "nixos-container status {0}".format(self.vm_id),
            capture_stdout=True, check=False).rstrip()
