# -*- coding: utf-8 -*-
#
# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# -----
#
# Certain portions of the contents of this file are derived from TPC-DS version 3.2.0
# (retrieved from www.tpc.org/tpc_documents_current_versions/current_specifications5.asp).
# Such portions are subject to copyrights held by Transaction Processing Performance Council (“TPC”)
# and licensed under the TPC EULA (a copy of which accompanies this file as “TPC EULA” and is also
# available at http://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp) (the “TPC EULA”).
#
# You may not use this file except in compliance with the TPC EULA.
# DISCLAIMER: Portions of this file is derived from the TPC-DS Benchmark and as such any results
# obtained using this file are not comparable to published TPC-DS Benchmark results, as the results
# obtained from using this file do not comply with the TPC-DS Benchmark.
#

import argparse
import subprocess
import shutil
import sys
import os
from multiprocessing import Process

def check_version():
    req_ver = (3,6)
    cur_ver = sys.version_info
    if cur_ver < req_ver:
        raise Exception('Minimum required Python version is 3.6, but current python version is {}.'
                        .format(str(cur_ver.major) + '.' + str(cur_ver.minor)) +
                        ' Please use proper Python version')

def check_build():
    # Check if necessary executable or jars are built.
    if not (os.path.exists('tpcds-gen/target/tpcds-gen-1.0-SNAPSHOT.jar') and
            os.path.exists('tpcds-gen/target/tools/dsdgen')):
        raise Exception('Target jar file is not found in `target` folder, ' +
                        'please refer to README document and build this project first.')


def generate_data(args):
    check_build()
    if args.type == 'hdfs':
        # Check if hadoop is installed.
        if shutil.which('hadoop') is None:
            raise Exception('No Hadoop binary found in current environment, ' +
                            'please install Hadoop for data generation in cluster.')
        # Submit hadoop MR job to generate data
        os.chdir('tpcds-gen')
        subprocess.run(['hadoop', 'jar', 'target/tpcds-gen-1.0-SNAPSHOT.jar',
                        '-d', args.data_dir, '-p', args.parallel, '-s', args.scale], check=True)
    if args.type == 'local':
        if not os.path.isdir(args.data_dir):
            os.makedirs(args.data_dir)
        if args.data_dir[0] == '/':
            data_dir = args.data_dir
        else:
            # add this because the dsdgen will be executed in a sub-folder
            data_dir = '../../../{}'.format(args.data_dir)

        os.chdir('tpcds-gen/target/tools')
        proc = []
        for i in range(1, int(args.parallel) + 1):
            dsdgen_args = ["-scale", args.scale, "-dir", data_dir, "-parallel", args.parallel, "-child", str(i), "-force", "Y", "-verbose", "Y"]
            proc.append(subprocess.Popen(["./dsdgen"] + dsdgen_args))

        # wait for data generation to complete
        for i in range(int(args.parallel)):
            proc[i].wait()
            if proc[i].returncode != 0:
                print("dsdgen failed with return code {}".format(proc[i].returncode))
                raise Exception("dsdgen failed")

        os.chdir('../../..')
        from ds_convert import get_schemas
        # move multi-partition files into table folders
        for table in get_schemas(use_decimal=True).keys():
            print('mkdir -p {}/{}'.format(args.data_dir, table))
            os.system('mkdir -p {}/{}'.format(args.data_dir, table))
            for i in range(1, int(args.parallel)+1):
                os.system('mv {}/{}_{}_{}.dat {}/{}/ 2>/dev/null'.format(args.data_dir, table, i, args.parallel, args.data_dir, table))
        # show summary
        os.system('du -h -d1 {}'.format(args.data_dir))


def generate_query(args):
    check_build()
    # copy tpcds.idx to working dir, it's required by TPCDS tool
    subprocess.run(['cp', './tpcds-gen/target/tools/tpcds.idx',
                   './tpcds.idx'], check=True)

    if not os.path.isdir(args.query_output_dir):
        os.makedirs(args.query_output_dir)
    subprocess.run(['./tpcds-gen/target/tools/dsqgen', '-template', args.template, '-directory',
                    args.template_dir, '-dialect', 'spark', '-scale', args.scale, '-output_dir',
                    args.query_output_dir], check=True)
    # remove it after use.
    subprocess.run(['rm', './tpcds.idx'], check=True)


def generate_query_streams(args):
    check_build()
    # Copy tpcds.idx to working dir, it's required by TPCDS tool.
    subprocess.run(['cp', './tpcds-gen/target/tools/tpcds.idx',
                   './tpcds.idx'], check=True)

    if not os.path.isdir(args.query_output_dir):
        os.makedirs(args.query_output_dir)

    subprocess.run(['./tpcds-gen/target/tools/dsqgen', '-scale', args.scale, '-directory',
                    args.template_dir, '-output_dir', args.query_output_dir, '-input',
                    os.path.join(args.template_dir,'templates.lst'),
                    '-dialect', 'spark', '-streams', args.streams], check=True)
    # Remove it after use.
    subprocess.run(['rm', './tpcds.idx'], check=True)


def convert_csv_to_parquet(args):
    # This will submit a Spark job to read the TPCDS raw data (csv with "|" delimiter) then save as Parquet files.
    # The configuration for this will be read from an external template file. User should set Spark parameters there.
    with open(args.spark_submit_template, 'r') as f:
        template = f.read()

    cmd = []
    cmd.append("--input-prefix " + args.input_prefix)
    if args.input_suffix != "":
        cmd.append("--input-suffix " + args.input_suffix)
    cmd.append("--output-prefix " + args.output_prefix)
    cmd.append("--report-file " + args.report_file)
    cmd.append("--log-level " + args.log_level)
    if args.non_decimal:
        cmd.append("--non-decimal")

    # run spark-submit
    cmd = template.strip() + "\n  ds_convert.py " + " ".join(cmd).strip()
    print(cmd)
    os.system(cmd)


def submit_one_stream(spark_submit_template_path,
                      input_prefix,
                      output_prefix,
                      output_format,
                      query_stream,
                      run_log_path,
                      time_log_path):
    # The configuration for this will be read from an external template file. User should set Spark
    # parameters there.
    with open(spark_submit_template_path, 'r') as f:
        template = f.read()
    cmd = []
    cmd.append("--input-prefix " + input_prefix)
    cmd.append("--time-log " + time_log_path)
    if output_prefix:
        cmd.append("--output-prefix " + output_prefix)
    if output_format:
        cmd.append("--output-format " + output_format)
    cmd.append("--query-stream " + query_stream)
    # run spark-submit
    cmd = template.strip() + "\n  power_run.py " + " ".join(cmd).strip()
    cmd += " 2>&1 | tee {}".format(run_log_path)
    print(cmd)
    subprocess.run(cmd, shell=True, check=True)

def power_run(args):
    # This will submit a Spark job to do the Power Run
    submit_one_stream(args.spark_submit_template,
                      args.input_prefix,
                      args.output_prefix,
                      args.output_format,
                      args.query_stream,
                      args.run_log,
                      args.time_log)


def throughput_run(args):
    streams = args.query_stream.split(',')
    # check if multiple streams are provided
    if len(streams) == 1:
        raise Exception('Throughput Run requires multiple query stream but only one is provided. ' +
            'Please use Power Run for one stream, or provide multiple query streams for Throughput Run.')
    # run queries together
    procs = []
    for stream in streams:
        # rename the log for each stream.
        # stream name: e.g. "./nds_query_streams/query_1.sql"
        # if args.run_log is "YOUR_RUN_LOG", the final renamed log file path will be e.g. "YOUR_RUN_LOG_query_1"
        run_log_path = args.run_log + '_{}'.format(stream.split('/')[-1][:-4])
        time_log_path = args.time_log + '_{}'.format(stream.split('/')[-1][:-4])
        p = Process(target=submit_one_stream, args=(args.spark_submit_template,
                                                    args.input_prefix,
                                                    args.output_prefix,
                                                    args.output_format,
                                                    stream,
                                                    run_log_path,
                                                    time_log_path))
        procs.append(p)
        p.start()

    for p in procs:
        p.join()
        

def main():
    check_version()
    parser = argparse.ArgumentParser(
        description='Argument parser for NDS benchmark options.')
    parser.add_argument('--generate', choices=['data', 'query', 'streams', 'convert'], 
                        help='generate tpc-ds data or queries.')
    parser.add_argument('--type', choices=['local', 'hdfs'], required='data' in sys.argv,
                        help='file system to save the generated data')
    parser.add_argument('--data-dir',
                        help='If generating data: target HDFS path for generated data.')
    parser.add_argument(
        '--template-dir', help='directory to find query templates.')
    parser.add_argument('--scale', help='volume of data to generate in GB.')
    parser.add_argument(
        '--parallel', help='generate data in n parallel MapReduce jobs.')
    parser.add_argument('--template', required='query' in sys.argv,
                        help='query template used to build queries.')
    parser.add_argument('--streams', help='generate how many query streams.')
    parser.add_argument('--query-output-dir',
                        help='directory to write query streams.')
    parser.add_argument('--spark-submit-template', required=('--run' in sys.argv) or ('convert' in sys.argv),
                        help='A Spark config template contains necessary Spark job configurations.')
    parser.add_argument('--output-mode',
                        help='Spark data source output mode for the result (default: overwrite)',
                        default="overwrite")
    parser.add_argument(
        '--input-prefix', help='text to prepend to every input file path (e.g., "hdfs:///ds-generated-data/"; the default is empty)', default="")
    parser.add_argument(
        '--input-suffix', help='text to append to every input filename (e.g., ".dat"; the default is empty)', default="")
    parser.add_argument(
        '--output-prefix', help='text to prepend to every output file (e.g., "hdfs:///ds-parquet/"; the default is empty)', default="")
    parser.add_argument(
        '--output-format', help='type of query output, e.g. csv, parquet, orc.')
    parser.add_argument(
        '--report-file', help='location in which to store a performance report', default='report.txt')
    parser.add_argument(
        '--log-level', help='set log level (default: OFF, same to log4j), possible values: OFF, ERROR, WARN, INFO, DEBUG, ALL', default="OFF")
    parser.add_argument('--run', choices=['power','throughput'], help='NDS run type')
    parser.add_argument(
        '--query-stream', help='query stream file that contains all NDS queries in specific order')
    parser.add_argument('--non-decimal', action='store_true',
                        help='replace DecimalType with DoubleType when saving parquet files. If not specified, decimal data will be saved.')
    parser.add_argument('--run-log', help='file to save run logs')
    parser.add_argument('--time-log', required='--run' in sys.argv, help='CSV file to save query and query execution time')
    args = parser.parse_args()

    if args.generate != None:
        if args.generate == 'data':
            generate_data(args)

        if args.generate == 'query':
            generate_query(args)

        if args.generate == 'streams':
            generate_query_streams(args)

        if args.generate == 'convert':
            convert_csv_to_parquet(args)
    else:
        if args.run == 'power':
            power_run(args)
        if args.run == 'throughput':
            throughput_run(args)


if __name__ == '__main__':
    main()
