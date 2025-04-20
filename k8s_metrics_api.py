from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
import requests
from kubernetes import client, config

app = Flask(__name__, static_url_path='')
CORS(app)  # Enable cross-origin requests

# Try to load in-cluster configuration
try:
    config.load_incluster_config()
    print("Running with in-cluster configuration")
except config.ConfigException:
    try:
        # Fall back to kubeconfig
        config.load_kube_config()
        print("Running with kubeconfig configuration")
    except config.ConfigException:
        print("Could not configure kubernetes client")

# Initialize Kubernetes API clients
v1 = client.CoreV1Api()
metrics_api = client.CustomObjectsApi()

# Define Prometheus endpoint - update with your actual service address
PROMETHEUS_URL = os.environ.get('PROMETHEUS_URL', 'http://kube-prom-stack-kube-prome-prometheus.observability.svc.cluster.local:9090')
NODE_EXPORTER_PORT = 9100

def query_prometheus(query):
    """Query Prometheus and return the result"""
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query})
        if response.status_code == 200:
            result = response.json()
            if result['status'] == 'success' and result['data']['result']:
                return result['data']['result']
        return None
    except Exception as e:
        print(f"Error querying Prometheus: {e}")
        return None

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/api/cluster-metrics')
def get_cluster_metrics():
    try:
        # Get nodes
        nodes = v1.list_node().items
        
        # Get node metrics
        node_metrics = metrics_api.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes"
        )
        
        # Get pods
        pods = v1.list_pod_for_all_namespaces().items

        # Process and format the data
        formatted_data = {
            'nodes': [],
            'summary': {
                'totalCpuUsage': 0,
                'totalRamUsage': 0,
                'totalStorageUsage': 0,
                'totalNetworkUsage': 0,
                'nodesOnline': 0,
                'nodesTotal': len(nodes),
                'podsRunning': 0
            }
        }
        
        # Process node data
        for node in nodes:
            node_name = node.metadata.name
            node_status = 'active'
            
            # Check if node is ready
            for condition in node.status.conditions:
                if condition.type == 'Ready' and condition.status != 'True':
                    node_status = 'danger'
                    break
            
            # Find metrics for this node
            cpu_usage = 0
            ram_usage = 0
            cpu_capacity = 0
            memory_capacity = 0
            
            try:
                # Parse CPU and memory capacity
                cpu_capacity = float(node.status.capacity['cpu'])
                memory_str = node.status.capacity['memory']
                # Convert Ki to GB
                if memory_str.endswith('Ki'):
                    memory_capacity = int(memory_str[:-2]) / (1024 * 1024)
                elif memory_str.endswith('Mi'):
                    memory_capacity = int(memory_str[:-2]) / 1024
                elif memory_str.endswith('Gi'):
                    memory_capacity = int(memory_str[:-2])
                else:
                    memory_capacity = int(memory_str) / (1024 * 1024 * 1024)
                
                # Find node metrics
                for item in node_metrics['items']:
                    if item['metadata']['name'] == node_name:
                        # Parse CPU usage (typically in nanocores or millicores)
                        cpu_str = item['usage']['cpu']
                        if cpu_str.endswith('n'):
                            cpu_usage = int(cpu_str[:-1]) / 1000000000
                        elif cpu_str.endswith('m'):
                            cpu_usage = int(cpu_str[:-1]) / 1000
                        else:
                            cpu_usage = float(cpu_str)
                        
                        # Parse memory usage
                        mem_str = item['usage']['memory']
                        if mem_str.endswith('Ki'):
                            ram_usage = int(mem_str[:-2]) / (1024 * 1024)
                        elif mem_str.endswith('Mi'):
                            ram_usage = int(mem_str[:-2]) / 1024
                        elif mem_str.endswith('Gi'):
                            ram_usage = int(mem_str[:-2])
                        else:
                            ram_usage = int(mem_str) / (1024 * 1024 * 1024)
            except Exception as e:
                print(f"Error parsing metrics for node {node_name}: {e}")
            
            # Calculate percentages
            cpu_percent = min(round((cpu_usage / max(cpu_capacity, 0.1)) * 100), 100)
            ram_percent = min(round((ram_usage / max(memory_capacity, 0.1)) * 100), 100)
            
            # Get IP address for node exporter queries
            ip_address = 'Unknown'
            for address in node.status.addresses:
                if address.type == 'InternalIP':
                    ip_address = address.address
                    break
            
            # Get storage metrics from Prometheus node-exporter
            storage_total = 100  # Default placeholder in GB
            storage_usage = 50   # Default placeholder percentage
            
            try:
                # Query Prometheus for filesystem size (root filesystem)
                fs_size_query = f'node_filesystem_size_bytes{{instance="{ip_address}:{NODE_EXPORTER_PORT}",mountpoint="/"}}'
                fs_size_result = query_prometheus(fs_size_query)
                
                # Query Prometheus for filesystem free space
                fs_free_query = f'node_filesystem_free_bytes{{instance="{ip_address}:{NODE_EXPORTER_PORT}",mountpoint="/"}}'
                fs_free_result = query_prometheus(fs_free_query)
                
                if fs_size_result and fs_free_result:
                    # Convert bytes to GB
                    fs_size_bytes = float(fs_size_result[0]['value'][1])
                    fs_free_bytes = float(fs_free_result[0]['value'][1])
                    fs_used_bytes = fs_size_bytes - fs_free_bytes
                    
                    storage_total = round(fs_size_bytes / (1024 * 1024 * 1024), 1)  # GB
                    storage_usage = round((fs_used_bytes / fs_size_bytes) * 100)  # Percentage
                else:
                    # Fallback for Raspberry Pi 5 with 100GB SD card (typical)
                    storage_total = 100
                    storage_usage = 50
            except Exception as e:
                print(f"Error getting storage metrics for node {node_name}: {e}")
            
            # Get network metrics from Prometheus node-exporter
            network_bandwidth = 50  # Default placeholder in MB/s
            
            try:
                # Query Prometheus for network receive rate (last 5 min)
                net_rx_query = f'rate(node_network_receive_bytes_total{{instance="{ip_address}:{NODE_EXPORTER_PORT}",device="eth0"}}[5m])'
                net_rx_result = query_prometheus(net_rx_query)
                
                # Query Prometheus for network transmit rate (last 5 min)
                net_tx_query = f'rate(node_network_transmit_bytes_total{{instance="{ip_address}:{NODE_EXPORTER_PORT}",device="eth0"}}[5m])'
                net_tx_result = query_prometheus(net_tx_query)
                
                if net_rx_result and net_tx_result:
                    # Convert bytes/sec to MB/s
                    net_rx_bytes_per_sec = float(net_rx_result[0]['value'][1])
                    net_tx_bytes_per_sec = float(net_tx_result[0]['value'][1])
                    
                    # Calculate total network bandwidth in MB/s
                    network_bandwidth = round((net_rx_bytes_per_sec + net_tx_bytes_per_sec) / (1024 * 1024), 1)
                else:
                    # Fallback for Raspberry Pi 5 with Gigabit Ethernet
                    # Typical usable bandwidth is around 50 MB/s due to SD card and other limitations
                    network_bandwidth = 50
            except Exception as e:
                print(f"Error getting network metrics for node {node_name}: {e}")
            
            # Update summary data
            formatted_data['summary']['totalCpuUsage'] += cpu_percent
            formatted_data['summary']['totalRamUsage'] += ram_percent
            formatted_data['summary']['totalStorageUsage'] += storage_usage
            formatted_data['summary']['totalNetworkUsage'] += network_bandwidth
            
            if node_status != 'danger':
                formatted_data['summary']['nodesOnline'] += 1
            
            # Determine warning or critical status based on usage
            if cpu_percent > 90 or ram_percent > 90:
                node_status = 'danger'
            elif cpu_percent > 80 or ram_percent > 80:
                node_status = 'warning'
            
            # Detect control plane nodes
            role = 'Worker'
            if node.metadata.labels and ('node-role.kubernetes.io/master' in node.metadata.labels or 
                                        'node-role.kubernetes.io/control-plane' in node.metadata.labels):
                role = 'Control Plane'
            
            # Add node data
            node_info = {
                'name': node_name,
                'role': role,
                'status': node_status,
                'cpuCores': cpu_capacity,
                'cpuUsage': cpu_percent,
                'ramTotal': round(memory_capacity),
                'ramUsage': ram_percent,
                'storageTotal': storage_total,
                'storageUsage': storage_usage,
                'networkBandwidth': network_bandwidth,
                'ipAddress': ip_address,
                'kernel': node.status.node_info.kernel_version,
                'uptime': "N/A"  # Uptime would require additional calculations
            }
            
            formatted_data['nodes'].append(node_info)
        
        # Count running pods
        formatted_data['summary']['podsRunning'] = sum(1 for pod in pods if pod.status.phase == 'Running')
        
        # Average the summary metrics
        if formatted_data['summary']['nodesTotal'] > 0:
            formatted_data['summary']['totalCpuUsage'] = round(formatted_data['summary']['totalCpuUsage'] / formatted_data['summary']['nodesTotal'])
            formatted_data['summary']['totalRamUsage'] = round(formatted_data['summary']['totalRamUsage'] / formatted_data['summary']['nodesTotal'])
            formatted_data['summary']['totalStorageUsage'] = round(formatted_data['summary']['totalStorageUsage'] / formatted_data['summary']['nodesTotal'])
        
        return jsonify(formatted_data)
    except Exception as e:
        print(f"Error fetching metrics: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Use environment variable for port or default to 8080
    port = int(os.environ.get('PORT', 8080))
    # In production, use gunicorn instead
    app.run(host='0.0.0.0', port=port)