import config
import logging
import bash
from cmd import dockercmd
import os
import utils
import math


class Prepare:
    def __init__(self, context):
        self.context = context

    def execute(self):
        logging.info('Begin of prepare step')

        prepare_simulation_dir()
        remove_old_containers_if_exists()
        recreate_network()

        utils.sleep(1)

        self.give_nodes_spendable_coins()

        self.start_nodes()

        logging.info('End of prepare step')

    def give_nodes_spendable_coins(self):
        nodes = list(self.context.all_bitcoin_nodes.values())

        for node in nodes:
            node.run()
            node.connect_to_rpc()

        for node in nodes:
            node.wait_until_rpc_ready()

        amount_of_tx_chains = calc_number_of_tx_chains(
            self.context.args.txs_per_tick,
            self.context.args.blocks_per_tick,
            len(nodes)
        )

        for i, node in enumerate(nodes):
            for node_to_be_connected in nodes[max(0, i - 5):i]:
                node.execute_rpc('addnode', str(node_to_be_connected.ip), 'add')
            wait_until_height_reached(node, i * amount_of_tx_chains)
            node.execute_rpc('generate', amount_of_tx_chains)

        wait_until_height_reached(nodes[0], amount_of_tx_chains * len(nodes))
        nodes[0].execute_rpc('generate', config.blocks_needed_to_make_coinbase_spendable)
        current_height = config.blocks_needed_to_make_coinbase_spendable + amount_of_tx_chains * len(nodes)

        for node in nodes:
            wait_until_height_reached(node, current_height)

        for node in nodes:
            node.set_spent_to_address()
            node.create_tx_chains()
            logging.info("Transferring coinbase tx to normal tx for node={}".format(node.name))
            node.transfer_coinbases_to_normal_tx()

        for i, node in enumerate(nodes):
            wait_until_height_reached(node, current_height + i)
            node.execute_rpc('generate', 1)

        current_height += len(nodes)
        self.context.first_block_height = current_height
        for node in nodes:
            wait_until_height_reached(node, current_height)
        delete_nodes(nodes)

    def start_nodes(self):
        nodes = self.context.all_bitcoin_nodes.values()
        for node in nodes:
            node.run()
            node.connect_to_rpc(timeout=0.25)

        for node in nodes:
            node.wait_until_rpc_ready()
            wait_until_height_reached(node, self.context.first_block_height)

        start_hash = self.context.one_normal_node.execute_rpc('getbestblockhash')
        for node in self.context.selfish_node_proxies.values():
            node.run(start_hash)

        for node in self.context.selfish_node_proxies.values():
            node.wait_for_highest_tip_of_node(self.context.one_normal_node)

        for node in self.context.nodes.values():
            node.connect()

        for node in self.context.all_public_nodes.values():
            node.add_latency(self.context.zone.zones)

        utils.sleep(3 + len(self.context.all_nodes) * 0.2)


def delete_nodes(nodes):
    for node in nodes:
        node.delete_peers_file()
        node.execute_rpc('stop')
        node.rm()


def prepare_simulation_dir():
    if not os.path.exists(config.sim_dir):
        os.makedirs(config.sim_dir)

    if os.path.islink(config.soft_link_to_sim_dir):
        bash.check_output('unlink {}'.format(config.soft_link_to_sim_dir))
    bash.check_output('ln -s {} {}'.format(config.sim_dir, config.soft_link_to_sim_dir))

    bash.check_output('cp {} {}'.format(config.network_csv, config.sim_dir))
    bash.check_output('cp {} {}'.format(config.ticks_csv, config.sim_dir))
    bash.check_output('cp {} {}'.format(config.nodes_json, config.sim_dir))
    bash.check_output('cp {} {}'.format(config.args_json, config.sim_dir))


def remove_old_containers_if_exists():
    containers = bash.check_output(dockercmd.ps_containers())
    if len(containers) > 0:
        bash.check_output(dockercmd.remove_all_containers(), lvl=logging.DEBUG)


def recreate_network():
    exit_code = bash.call_silent(dockercmd.inspect_network())
    if exit_code == 0:
        bash.check_output(dockercmd.rm_network())
    bash.check_output(dockercmd.create_network())


def wait_until_height_reached(node, height):
    while int(node.execute_rpc('getblockcount')) < height:
        logging.debug('Waiting until height={} is reached...'.format(str(height)))
        utils.sleep(0.2)


def calc_number_of_tx_chains(txs_per_tick, blocks_per_tick, number_of_nodes):
    txs_per_block = txs_per_tick/blocks_per_tick
    txs_per_block_per_node = txs_per_block/number_of_nodes

    # 3 times + 3 chains in reserve
    needed_tx_chains = (txs_per_block_per_node / config.max_in_mempool_ancestors) * 3 + 3

    return math.ceil(needed_tx_chains)
